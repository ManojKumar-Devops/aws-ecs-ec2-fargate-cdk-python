from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)

class GithubRunnerStack(Stack):
    """
    Self-hosted GitHub Actions runners on EC2 ASG with MixedInstancesPolicy:
    - OnDemand base capacity + Spot above base
    - On-demand scaling controlled by workflow (set desired-capacity up/down)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        owner: str,
        repo: str,
        github_pat_secret_name: str = "github/pat",
        runner_labels: str = "lab-runner,ec2",
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # PAT stored in Secrets Manager:
        # SecretString must be JSON: {"token":"ghp_..."}
        gh_pat_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "GitHubPatSecret", github_pat_secret_name
        )

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        role = iam.Role(
            self, "RunnerInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )

        # For runner bootstrap + optional debugging via SSM
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))
        gh_pat_secret.grant_read(role)

        # User data: install runner, register to repo, run as service
        ud = ec2.UserData.for_linux()
        ud.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y curl jq git",

            "mkdir -p /opt/actions-runner && cd /opt/actions-runner",

            # Pin a version (update when needed)
            "RUNNER_VERSION=2.319.1",
            "curl -L -o actions-runner-linux-x64.tar.gz "
            "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "tar xzf actions-runner-linux-x64.tar.gz",

            # Get PAT from Secrets Manager
            f"TOKEN_JSON=$(aws secretsmanager get-secret-value --secret-id {github_pat_secret_name} --query SecretString --output text)",
            "GITHUB_PAT=$(echo $TOKEN_JSON | jq -r .token)",

            # Request a runner registration token for the repo
            f"OWNER={owner}",
            f"REPO={repo}",
            "REG_TOKEN=$(curl -s -X POST "
            "-H \"Authorization: token $GITHUB_PAT\" "
            "https://api.github.com/repos/$OWNER/$REPO/actions/runners/registration-token | jq -r .token)",

            # Configure runner
            f"./config.sh --unattended --url https://github.com/{owner}/{repo} "
            "--token $REG_TOKEN "
            f"--labels {runner_labels} "
            "--name $(hostname)",

            # Run as a service
            "./svc.sh install",
            "./svc.sh start",
        )

        lt = ec2.LaunchTemplate(
            self, "RunnerLaunchTemplate",
            machine_image=ec2.AmazonLinux2ImageSsmParameter(),
            instance_type=ec2.InstanceType("t3.large"),
            role=role,
            security_group=sg,
            user_data=ud,
        )

        # MixedInstancesPolicy ASG
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
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3.large")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3a.large")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("m5.large")),
                ],
            ),
            health_check=autoscaling.HealthCheck.ec2(grace=Duration.minutes(5)),
        )

        CfnOutput(self, "RunnerAsgName", value=asg.auto_scaling_group_name)
