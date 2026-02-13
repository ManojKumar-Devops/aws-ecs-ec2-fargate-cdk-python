# infra/stacks/github_runners_stack.py
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_ssm as ssm,
    aws_autoscaling as autoscaling,
)

class GithubRunnersStack(Stack):
    """
    Creates an autoscaling group of GitHub self-hosted runners.
    - Uses a Launch Template (so we can pass user-data + mixed instances)
    - Uses CfnAutoScalingGroup for MixedInstancesPolicy (spot + on-demand)
    Fill in:
      - ssm parameter name for GITHUB_RUNNER_TOKEN (or use Secrets Manager)
      - runner labels (lab-runner)
      - replace AMI id / instanceProfileName where necessary
    """
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, *,
                 github_runner_token_ssm_param: str="/github/runner/token",
                 instance_profile_name: str="REPLACE_ME_INSTANCE_PROFILE",
                 allowed_instance_types=None,
                 desired_capacity=1,
                 max_capacity=3,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if allowed_instance_types is None:
            allowed_instance_types = ["t3.small", "t3a.small"]

        # Simple role for runner instances (allow read SSM param and CloudWatch logs)
        runner_role = iam.Role(self, "RunnerInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"),
            ]
        )

        # If you prefer a pre-created instance profile, replace this
        instance_profile = iam.CfnInstanceProfile(self, "RunnerInstanceProfile",
            roles=[runner_role.role_name],
            instance_profile_name=f"{self.stack_name}-runner-profile"
        )

        # Use a Golden AMI if you have one; fallback to Amazon Linux 2
        # Replace with your golden AMI id if you have it
        ami = ec2.MachineImage.latest_amazon_linux(
            generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2,
        )

        # Security Group for runners (allow egress)
        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)
        sg.add_ingress_rule(ec2.Peer.ipv4("10.0.0.0/8"), ec2.Port.tcp(22), "SSH from VPC (adjust)")

        # UserData to register runner. The registration token is retrieved from SSM.
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -xe",
            # install dependencies: docker, jq, unzip, git, etc.
            "yum update -y || apt-get update -y",
            "yum install -y docker jq || apt-get install -y docker.io jq",
            "systemctl enable docker || true",
            "systemctl start docker || true",

            # fetch token from SSM
            f'GITHUB_RUNNER_TOKEN=$(aws ssm get-parameter --name "{github_runner_token_ssm_param}" --with-decryption --query "Parameter.Value" --output text || echo "")',
            "if [ -z \"$GITHUB_RUNNER_TOKEN\" ]; then echo 'Missing GitHub runner token in SSM' && exit 1; fi",

            # set runner vars (edit owner/repo or org)
            "GITHUB_OWNER_OR_REPO=REPLACE_WITH_OWNER_OR_REPO",  # e.g., my-org/my-repo or org name for org-level runners
            "RUNNER_LABELS='self-hosted,linux,x64,lab-runner'",
            "ARCH=$(uname -m)",
            "if [ \"$ARCH\" = 'x86_64' ]; then ARCH=x64; fi",

            # download runner
            "mkdir -p /opt/github-runner && cd /opt/github-runner",
            "RUNNER_VER=$(curl -s https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name)",
            "curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/${RUNNER_VER}/actions-runner-linux-${ARCH}-${RUNNER_VER#v}.tar.gz",
            "tar xzf actions-runner.tar.gz",

            # configure runner
            "cat > run_runner.sh <<'EOF'\n#!/bin/bash\nset -e\ncd /opt/github-runner\n./config.sh --url https://github.com/${GITHUB_OWNER_OR_REPO} --token ${GITHUB_RUNNER_TOKEN} --labels ${RUNNER_LABELS} --unattended --work _work\n./svc.sh install\n./svc.sh start\nEOF",
            "chmod +x run_runner.sh",
            "/opt/github-runner/run_runner.sh &",
        )

        # Launch template
        lt = ec2.CfnLaunchTemplate(self, "RunnerLaunchTemplate",
            launch_template_name=f"{self.stack_name}-runner-lt",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                instance_type=allowed_instance_types[0],
                image_id=ami.get_image(self).image_id if isinstance(ami, ec2.MachineImage) else None,
                iam_instance_profile=ec2.CfnLaunchTemplate.IamInstanceProfileProperty(
                    name=instance_profile.ref
                ),
                user_data=ec2.UserData.custom(user_data.render()).render(),
                security_group_ids=[sg.security_group_id],
            )
        )

        # MixedInstancesPolicy via CfnAutoScalingGroup
        # Uses the LaunchTemplate above and multiple instance types (overrides)
        overrides = []
        for it in allowed_instance_types:
            overrides.append({"InstanceType": it})

        cfn_asg = autoscaling.CfnAutoScalingGroup(self, "RunnerCfnASG",
            min_size=str(0),
            max_size=str(max_capacity),
            desired_capacity=str(desired_capacity),
            mixed_instances_policy=autoscaling.CfnAutoScalingGroup.MixedInstancesPolicyProperty(
                launch_template=autoscaling.CfnAutoScalingGroup.LaunchTemplateProperty(
                    launch_template_specification=autoscaling.CfnAutoScalingGroup.LaunchTemplateSpecificationProperty(
                        launch_template_id=lt.ref,
                        version=lt.attr_latest_version_number
                    ),
                    overrides=[autoscaling.CfnAutoScalingGroup.OverridesProperty(instance_type=it) for it in allowed_instance_types]
                ),
                instances_distribution=autoscaling.CfnAutoScalingGroup.InstancesDistributionProperty(
                    on_demand_allocation_strategy="prioritized",
                    on_demand_base_capacity=1,
                    on_demand_percentage_above_base_capacity=20,
                    spot_allocation_strategy="capacity-optimized"
                )
            ),
            vpc_zone_identifier=[s.subnet_id for s in vpc.public_subnets],
            tags=[
                autoscaling.CfnAutoScalingGroup.TagsProperty(
                    key="Name", value=f"{self.stack_name}-runner", propagate_at_launch=True
                )
            ]
        )

        CfnOutput(self, "RunnerSSMParam", value=github_runner_token_ssm_param)
        CfnOutput(self, "RunnerSecurityGroup", value=sg.security_group_id)
