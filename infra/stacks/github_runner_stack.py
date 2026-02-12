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

        # PAT stored in SSM SecureString
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

        # Read PAT from SSM SecureString
        role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{pat_param_name}"],
        ))

        # Often required to decrypt SecureString (lab-friendly; tighten later)
        role.add_to_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"],
            resources=["*"],
        ))

        # Give the runner enough power to run your CDK deploys (lab)
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("PowerUserAccess"))

        instance_profile = iam.CfnInstanceProfile(
            self, "RunnerInstanceProfile",
            roles=[role.role_name],
        )

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

            # runner install
            "mkdir -p /opt/actions-runner",
            "cd /opt/actions-runner",
            "RUNNER_VERSION=2.316.1",
            "curl -L -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "chown -R ec2-user:ec2-user /opt/actions-runner",

            # bootstrap
            "cat > /opt/actions-runner/bootstrap.sh <<'EOF'\n"
            "#!/usr/bin/env bash\n"
            "set -euxo pipefail\n"
            f"GITHUB_OWNER='{github_owner}'\n"
            f"GITHUB_REPO='{github_repo}'\n"
            f"RUNNER_LABELS='{runner_labels}'\n"
            f"PAT_PARAM_NAME='{pat_param_name}'\n"
            "\n"
            "PAT=$(aws ssm get-parameter --name \"$PAT_PARAM_NAME\" --with-decryption --query Parameter.Value --output text)\n"
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

        # AMI for LaunchTemplate
        ami = ec2.MachineImage.latest_amazon_linux2().get_image(self).image_id

        # Launch Template (required for MixedInstancesPolicy)
        lt = ec2.CfnLaunchTemplate(
            self, "RunnerLaunchTemplate",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                image_id=ami,
                instance_type="t3.large",  # base type; overrides will add others
                security_group_ids=[sg.security_group_id],
                iam_instance_profile=ec2.CfnLaunchTemplate.IamInstanceProfileProperty(
                    name=instance_profile.ref
                ),
                user_data=Fn.base64(user_data.render()),
            )
        )

        public_subnet_ids = [subnet.subnet_id for subnet in vpc.public_subnets]

        # MixedInstancesPolicy ASG (On-Demand + Spot)
        autoscaling.CfnAutoScalingGroup(
            self, "RunnerAsg",
            vpc_zone_identifier=public_subnet_ids,
            min_size="0",
            max_size="3",
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
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="m5.large"),
                    ]
                )
            )
        )

        CfnOutput(self, "RunnerLabels", value=runner_labels)
        CfnOutput(self, "RunnerRepo", value=f"{github_owner}/{github_repo}")
        CfnOutput(self, "PatParam", value=pat_param_name)
