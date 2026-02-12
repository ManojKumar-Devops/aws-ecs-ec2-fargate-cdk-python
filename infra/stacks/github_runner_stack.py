import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Fn,
    aws_ec2 as ec2,
    aws_iam as iam,
)

class GithubRunnerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        github_owner = self.node.try_get_context("github_owner") or os.getenv("REPO_OWNER")
        github_repo = self.node.try_get_context("github_repo") or os.getenv("REPO_NAME")
        runner_labels = self.node.try_get_context("runner_labels") or os.getenv("RUNNER_LABELS") or "lab-runner"
        reg_token = self.node.try_get_context("runner_reg_token") or os.getenv("RUNNER_REG_TOKEN")

        # spot/on-demand switch
        use_spot = (self.node.try_get_context("use_spot") or "true").lower() == "true"

        if not github_owner or not github_repo or not reg_token:
            raise ValueError("Missing github_owner/github_repo/runner_reg_token (context)")

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH (optional)")

        role = iam.Role(
            self, "RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                # If your runner needs to deploy stacks itself, add PowerUserAccess,
                # but this may be restricted in some labs.
                # iam.ManagedPolicy.from_aws_managed_policy_name("PowerUserAccess"),
            ],
        )

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y docker git jq curl unzip tar",
            "systemctl enable docker",
            "systemctl start docker",
            "usermod -aG docker ec2-user",

            "curl -fsSL https://rpm.nodesource.com/setup_18.x | bash -",
            "yum install -y nodejs",
            "npm install -g aws-cdk",

            "yum install -y python3 python3-pip || true",
            "python3 -m pip install --upgrade pip",

            "mkdir -p /opt/actions-runner",
            "cd /opt/actions-runner",
            "RUNNER_VERSION=2.316.1",
            "curl -L -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "chown -R ec2-user:ec2-user /opt/actions-runner",

            "cat > /opt/actions-runner/bootstrap.sh <<'EOF'\n"
            "#!/usr/bin/env bash\n"
            "set -euxo pipefail\n"
            f"GITHUB_OWNER='{github_owner}'\n"
            f"GITHUB_REPO='{github_repo}'\n"
            f"RUNNER_LABELS='{runner_labels}'\n"
            f"REG_TOKEN='{reg_token}'\n"
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

        # Instance (no ASG)
        instance = ec2.Instance(
            self, "RunnerInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.large"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            security_group=sg,
            role=role,
            user_data=user_data,
        )

        # Apply Spot via low-level override
        if use_spot:
            cfn_inst = instance.node.default_child
            cfn_inst.add_property_override("InstanceMarketOptions", {
                "MarketType": "spot",
                "SpotOptions": {
                    "SpotInstanceType": "one-time",
                    "InstanceInterruptionBehavior": "terminate"
                }
            })

        CfnOutput(self, "RunnerInstanceId", value=instance.instance_id)
        CfnOutput(self, "RunnerLabels", value=runner_labels)
        CfnOutput(self, "RunnerRepo", value=f"{github_owner}/{github_repo}")
        CfnOutput(self, "RunnerSpot", value=str(use_spot).lower())
