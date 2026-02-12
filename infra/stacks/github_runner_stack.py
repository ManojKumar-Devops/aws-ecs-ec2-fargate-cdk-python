import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Fn,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_autoscaling as autoscaling,
)

class GithubRunnerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        github_owner = self.node.try_get_context("github_owner") or os.getenv("REPO_OWNER")
        github_repo = self.node.try_get_context("github_repo") or os.getenv("REPO_NAME")
        runner_labels = self.node.try_get_context("runner_labels") or os.getenv("RUNNER_LABELS") or "lab-runner"

        # NEW: registration token injected by workflow
        reg_token = self.node.try_get_context("runner_reg_token") or os.getenv("RUNNER_REG_TOKEN")

        if not github_owner or not github_repo or not reg_token:
            raise ValueError("Missing github_owner/github_repo/runner_reg_token (context) or env vars")

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        # IMPORT/CREATE role without inline policies (since your account blocks iam:PutRolePolicy)
        # Use a minimal role with SSM core only
        role = iam.Role(
            self, "RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        instance_profile = iam.CfnInstanceProfile(self, "RunnerInstanceProfile", roles=[role.role_name])

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y docker git jq curl unzip tar",
            "systemctl enable docker",
            "systemctl start docker",
            "usermod -aG docker ec2-user",

            # node + cdk (optional)
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

            # Register using the injected token
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

        ami = ec2.MachineImage.latest_amazon_linux2().get_image(self).image_id

        lt = ec2.CfnLaunchTemplate(
            self, "RunnerLaunchTemplate",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                image_id=ami,
                instance_type="t3.large",
                security_group_ids=[sg.security_group_id],
                iam_instance_profile=ec2.CfnLaunchTemplate.IamInstanceProfileProperty(
                    name=instance_profile.ref
                ),
                user_data=Fn.base64(user_data.render()),
            )
        )

        public_subnet_ids = [subnet.subnet_id for subnet in vpc.public_subnets]

        autoscaling.CfnAutoScalingGroup(
            self, "RunnerAsg",
            vpc_zone_identifier=public_subnet_ids,
            min_size="0",
            max_size="2",
            desired_capacity="1",
            mixed_instances_policy=autoscaling.CfnAutoScalingGroup.MixedInstancesPolicyProperty(
                instances_distribution=autoscaling.CfnAutoScalingGroup.InstancesDistributionProperty(
                    on_demand_base_capacity=1,
                    on_demand_percentage_above_base_capacity=20,
                    spot_allocation_strategy="capacity-optimized",
                ),
                launch_template=autoscaling.CfnAutoScalingGroup.LaunchTemplateProperty(
                    launch_template_specification=autoscaling.CfnAutoScalingGroup.LaunchTemplateSpecificationProperty(
                        launch_template_id=lt.ref,
                        version=lt.attr_latest_version_number,
                    ),
                    overrides=[
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="t3.large"),
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="t3.xlarge"),
                    ],
                ),
            ),
        )

        CfnOutput(self, "RunnerLabels", value=runner_labels)
        CfnOutput(self, "RunnerRepo", value=f"{github_owner}/{github_repo}")
