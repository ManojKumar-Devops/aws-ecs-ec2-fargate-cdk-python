from constructs import Construct
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
    aws_ssm as ssm,
)

class GithubRunnersStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Store GitHub runner registration token OR PAT in SSM (recommended)
        # For real prod: rotate tokens; consider ephemeral runners
        github_token_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "GithubTokenParam",
            parameter_name="/github/actions/runner-token",  # you create this in SSM
            version=1,
        )

        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        role = iam.Role(
            self, "RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
        )
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))
        github_token_param.grant_read(role)

        ud = ec2.UserData.for_linux()
        ud.add_commands(
            "yum update -y",
            "yum install -y git jq",
            "yum install -y docker",
            "systemctl enable docker",
            "systemctl start docker",
            "usermod -aG docker ec2-user",
            # Install runner
            "mkdir -p /opt/actions-runner && cd /opt/actions-runner",
            "curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.317.0/actions-runner-linux-x64-2.317.0.tar.gz",
            "tar xzf actions-runner.tar.gz",
            # Fetch token from SSM
            f"TOKEN=$(aws ssm get-parameter --name {github_token_param.parameter_name} --with-decryption --query 'Parameter.Value' --output text)",
            # Configure runner (REPLACE with your org/repo)
            "REPO_URL='https://github.com/ManojKumar-Devops/hello-devops-aws'",
            "RUNNER_NAME=$(hostname)",
            "./config.sh --unattended --url $REPO_URL --token $TOKEN --name $RUNNER_NAME --labels selfhosted,ec2,spotmix --work _work",
            "./svc.sh install",
            "./svc.sh start",
        )

        lt = autoscaling.LaunchTemplate(
            self, "RunnerLt",
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            user_data=ud,
            role=role,
            security_group=sg,
        )

        # MixedInstancesPolicy (On-Demand + Spot)
        autoscaling.AutoScalingGroup(
            self, "RunnerAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_capacity=0,
            desired_capacity=0,
            max_capacity=5,
            mixed_instances_policy=autoscaling.MixedInstancesPolicy(
                launch_template=lt,
                instances_distribution=autoscaling.InstancesDistribution(
                    on_demand_base_capacity=1,          # always keep 1 On-Demand when scaled up
                    on_demand_percentage_above_base_capacity=20,  # rest mostly Spot
                    spot_allocation_strategy=autoscaling.SpotAllocationStrategy.CAPACITY_OPTIMIZED,
                ),
                launch_template_overrides=[
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3.micro")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3.small")),
                    autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType("t3a.micro")),
                ],
            ),
        )
