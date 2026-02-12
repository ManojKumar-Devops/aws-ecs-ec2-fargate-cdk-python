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

        # Context/env
        github_owner = self.node.try_get_context("github_owner") or os.getenv("REPO_OWNER")
        github_repo = self.node.try_get_context("github_repo") or os.getenv("REPO_NAME")
        runner_labels = self.node.try_get_context("runner_labels") or os.getenv("RUNNER_LABELS") or "lab-runner"

        # NEW: PAT stored in SSM Parameter Store
        pat_param_name = self.node.try_get_context("github_pat_ssm_param") or os.getenv("RUNNER_PAT_SSM_PARAM")
        if not github_owner or not github_repo or not pat_param_name:
            raise ValueError("Missing github_owner/github_repo/github_pat_ssm_param (context) or env vars")

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        role = iam.Role(
            self, "RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # Allow EC2 instance to read PAT from SSM SecureString
        role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{pat_param_name}"],
        ))

        # Allow decrypt for SecureString (default SSM key is aws/ssm; kms:Decrypt needed in some accounts)
        role.add_to_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"],
            resources=["*"],
        ))

        # Allow deployments from runner (broad for lab; tighten later)
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

            # python
            "yum install -y python3 python3-pip || true",
            "python3 -m pip install --upgrade pip",

            # GitHub runner
            "mkdir -p /opt/actions-runner",
            "cd /opt/actions-runner",
            "RUNNER_VERSION=2.316.1",
            "curl -L -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "chown -R ec2-user:ec2-user /opt/actions-runner",

            # Bootstrap script (SSM)
            "cat > /opt/actions-runner/bootstrap.sh <<'EOF'\n"
            "#!/usr/bin/env bash\n"
            "set -euxo pipefail\n"
            f"GITHUB_OWNER='{github_owner}'\n"
            f"GITHUB_REPO='{github_repo}'\n"
            f"RUNNER_LABELS='{runner_labels}'\n"
            f"PAT_PARAM_NAME='{pat_param_name}'\n"
            "\n"
            "# Fetch PAT from SSM SecureString\n"
            "PAT=$(aws ssm get-parameter --name \"$PAT_PARAM_NAME\" --with-decryption --query Parameter.Value --output text)\n"
            "\n"
            "# Get runner registration token\n"
            "REG_TOKEN=$(curl -sS -X POST \\\n"
            "  -H \"Authorization: token ${PAT}\" \\\n"
            "  -H \"Accept: application/vnd.github+json\" \\\n"
            "  https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runners/registration-token | jq -r .token)\n"
            "\n"
            "cd /opt/actions-runner\n"
            "sudo -u ec2-user ./config.sh --url https://github.com/${GITHUB_OWNER}/${GITHUB_REPO} \\\n"
            "  --token ${REG_TOKEN} \\\n"
            "  --labels ${RUNNER_LABELS} \\\n"
            "  --unattended \\\n"
            "  --name $(hostname)\n"
            "\n"
            "./svc.sh install ec2-user\n"
            "./svc.sh start\n"
            "EOF",
            "chmod +x /opt/actions-runner/bootstrap.sh",
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

        # MixedInstancesPolicy (On-Demand base + Spot)
        cfn_asg = asg.node.default_child
        cfn_asg.add_property_override("MixedInstancesPolicy", {
            "InstancesDistribution": {
                "OnDemandBaseCapacity": 1,
                "OnDemandPercentageAboveBaseCapacity": 20,
                "SpotAllocationStrategy": "capacity-optimized",
            },
            "LaunchTemplate": {
                "LaunchTemplateSpecification": {
                    "LaunchTemplateId": {"Ref": "RunnerAsgLaunchConfig"},
                    "Version": {"Fn::GetAtt": ["RunnerAsgLaunchConfig", "LatestVersionNumber"]},
                },
                "Overrides": [
                    {"InstanceType": "t3.large"},
                    {"InstanceType": "t3.xlarge"},
                    {"InstanceType": "m5.large"},
                ],
            },
        })

        CfnOutput(self, "RunnerLabels", value=runner_labels)
        CfnOutput(self, "RunnerRepo", value=f"{github_owner}/{github_repo}")
        CfnOutput(self, "PatParam", value=pat_param_name)
