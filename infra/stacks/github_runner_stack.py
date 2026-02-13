from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
)

class GithubRunnerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        owner: str,
        repo: str,
        instance_profile_name: str,  # <-- existing instance profile (MANUAL)
        github_pat_secret_name: str = "github/pat",
        runner_labels: str = "lab-runner,ec2",
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        ud = ec2.UserData.for_linux()
        ud.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y curl jq git",

            "mkdir -p /opt/actions-runner && cd /opt/actions-runner",
            "RUNNER_VERSION=2.319.1",
            "curl -L -o actions-runner-linux-x64.tar.gz "
            "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64.tar.gz",

            f"TOKEN_JSON=$(aws secretsmanager get-secret-value --secret-id {github_pat_secret_name} --query SecretString --output text)",
            "GITHUB_PAT=$(echo $TOKEN_JSON | jq -r .token)",

            f"OWNER={owner}",
            f"REPO={repo}",
            "REG_TOKEN=$(curl -s -X POST "
            "-H \"Authorization: token $GITHUB_PAT\" "
            "https://api.github.com/repos/$OWNER/$REPO/actions/runners/registration-token | jq -r .token)",

            f"./config.sh --unattended --url https://github.com/{owner}/{repo} "
            "--token $REG_TOKEN "
            f"--labels {runner_labels} "
            "--name $(hostname)",

            "./svc.sh install",
            "./svc.sh start",
        )

        # L1 Launch Template so we can set IamInstanceProfile WITHOUT creating IAM resources
        lt = ec2.CfnLaunchTemplate(
            self, "RunnerLaunchTemplate",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                image_id=ec2.MachineImage.latest_amazon_linux2().get_image(self).image_id,
                instance_type="t3.large",
                iam_instance_profile=ec2.CfnLaunchTemplate.IamInstanceProfileProperty(
                    name=instance_profile_name
                ),
                security_group_ids=[sg.security_group_id],
                user_data=ec2.Fn.base64(ud.render()),
            ),
        )

        # MixedInstancesPolicy ASG using L1 launch template
        asg = autoscaling.CfnAutoScalingGroup(
            self, "RunnerAsg",
            min_size="0",
            max_size="5",
            desired_capacity="0",
            vpc_zone_identifier=vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC).subnet_ids,
            mixed_instances_policy=autoscaling.CfnAutoScalingGroup.MixedInstancesPolicyProperty(
                launch_template=autoscaling.CfnAutoScalingGroup.LaunchTemplateProperty(
                    launch_template_specification=autoscaling.CfnAutoScalingGroup.LaunchTemplateSpecificationProperty(
                        launch_template_id=lt.ref,
                        version=lt.attr_latest_version_number,
                    ),
                    overrides=[
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="t3.large"),
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="t3a.large"),
                        autoscaling.CfnAutoScalingGroup.LaunchTemplateOverridesProperty(instance_type="m5.large"),
                    ],
                ),
                instances_distribution=autoscaling.CfnAutoScalingGroup.InstancesDistributionProperty(
                    on_demand_base_capacity=1,
                    on_demand_percentage_above_base_capacity=20,
                    spot_allocation_strategy="capacity-optimized",
                ),
            ),
            health_check_type="EC2",
            health_check_grace_period=300,
        )

        CfnOutput(self, "RunnerAsgName", value=asg.ref)
