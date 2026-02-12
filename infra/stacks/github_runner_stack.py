import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
)

class GithubRunnerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Required context/env
        # Example: -c github_owner=ManojKumar-Devops -c github_repo=aws-ci-cd -c runner_labels=lab-runner
        github_owner = self.node.try_get_context("github_owner") or os.getenv("GITHUB_OWNER")
        github_repo = self.node.try_get_context("github_repo") or os.getenv("GITHUB_REPO")
        runner_labels = self.node.try_get_context("runner_labels") or os.getenv("RUNNER_LABELS") or "lab-runner"
        secret_arn = self.node.try_get_context("github_pat_secret_arn") or os.getenv("GITHUB_PAT_SECRET_ARN")

        if not github_owner or not github_repo or not secret_arn:
            raise ValueError("Missing github_owner/github_repo/github_pat_secret_arn (context) or env vars")

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)
        # outbound only is fine; runner pulls from GitHub + AWS APIs

        role = iam.Role(
            self, "RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # Allow reading PAT from Secrets Manager
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[secret_arn],
        ))

        # Allow deployments from runner (adjust least-privilege later)
        # Minimum for your CDK/CF deploy + ECS/ECR + CodeDeploy (broad-ish)
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("PowerUserAccess"))
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("IAMReadOnlyAccess"))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y docker git jq curl unzip tar",
            "systemctl enable docker",
            "systemctl start docker",
            "usermod -aG docker ec2-user",

            # node + cdk
            "curl -fsSL https://rpm.nodesource.com/setup_18.x | bash -",
            "yum install -y nodejs",
            "npm install -g aws-cdk",

            # python 3.11 (amazon linux2 has 3.7 by default; install via amazon-linux-extras or packages)
            "yum install -y python3 python3-pip || true",
            "python3 -m pip install --upgrade pip",

            # GitHub runner
            "mkdir -p /opt/actions-runner",
            "cd /opt/actions-runner",
            "RUNNER_VERSION=2.316.1",
            "curl -L -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "chown -R ec2-user:ec2-user /opt/actions-runner",

            # Create bootstrap script
            "cat > /opt/actions-runner/bootstrap.sh <<'EOF'\n"
            "#!/usr/bin/env bash\n"
            "set -euxo pipefail\n"
            "\n"
            f"GITHUB_OWNER='{github_owner}'\n"
            f"GITHUB_REPO='{github_repo}'\n"
            f"RUNNER_LABELS='{runner_labels}'\n"
            f"SECRET_ARN='{secret_arn}'\n"
            "\n"
            "# Fetch PAT from Secrets Manager (plain string secret)\n"
            "PAT=$(aws secretsmanager get-secret-value --secret-id \"$SECRET_ARN\" --query SecretString --output text)\n"
            "\n"
            "# Get registration token\n"
            "REG_TOKEN=$(curl -sS -X POST \\\n"
            "  -H \"Authorization: token ${PAT}\" \\\n"
            "  -H \"Accept: application/vnd.github+json\" \\\n"
            "  https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runners/registration-token | jq -r .token)\n"
            "\n"
            "cd /opt/actions-runner\n"
            "\n"
            "# Configure runner\n"
            "sudo -u ec2-user ./config.sh --url https://github.com/${GITHUB_OWNER}/${GITHUB_REPO} \\\n"
            "  --token ${REG_TOKEN} \\\n"
            "  --labels ${RUNNER_LABELS} \\\n"
            "  --unattended \\\n"
            "  --name $(hostname)\n"
            "\n"
            "# Install and start as a service\n"
            "./svc.sh install ec2-user\n"
            "./svc.sh start\n"
            "EOF",
            "chmod +x /opt/actions-runner/bootstrap.sh",

            # Run bootstrap once
            "/opt/actions-runner/bootstrap.sh",
        )

        asg = autoscaling.AutoScalingGroup(
            self, "RunnerAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_capacity=0,
            desired_capacity=1,
            max_capacity=3,
            instance_type=ec2.InstanceType("t3.large"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            role=role,
            security_group=sg,
            user_data=user_data,
        )

        # MixedInstancesPolicy (OD base + S
