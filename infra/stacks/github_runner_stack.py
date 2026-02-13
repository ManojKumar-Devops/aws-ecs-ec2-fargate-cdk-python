from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
)

class GithubRunnerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        owner: str,
        repo: str,
        runner_instance_role_arn: str,
        github_pat_secret_name: str = "github/pat",
        runner_labels: str = "lab-runner,ec2",
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # IMPORT role (no inline policy changes!)
        runner_role = iam.Role.from_role_arn(
            self, "ImportedRunnerRole",
            role_arn=runner_instance_role_arn,
            mutable=False,
        )

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

            # Read PAT secret using instance role credentials
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

        lt = ec2.LaunchTemplate(
            self, "RunnerLaunchTemplate",
            machine_image=ec2.AmazonLinux2ImageSsmParameter(),
            instance_type=ec2.InstanceType("t3.large"),
            role=runner_role,
            security_group=sg,
            user_data=ud,
        )

        asg = autoscaling.AutoScalingGroup(
            self, "RunnerAsg",
            vpc=vpc,
            min_capacity=0,
            desired_capacity=0,
            max_capacity=5,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            mixed_instances_policy=autoscaling.MixedInstancesPolicy(
                launch_template=lt,
                instances_distribution=autoscaling.InstancesDistribution(
                    on_demand_base_capacity=1,
                    on_demand_percentage_above_base_capacity=20,
                    spot_allocation_strategy=autoscaling.SpotAllocationStrategy.CAPACITY_OPTIMIZED,
                ),
                launch_template_overrides=[
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3.micro")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t2.micro")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3.medium")),
                ],
            ),
            health_check=autoscaling.HealthCheck.ec2(grace=Duration.minutes(5)),
        )

        CfnOutput(self, "RunnerAsgName", value=asg.auto_scaling_group_name)
