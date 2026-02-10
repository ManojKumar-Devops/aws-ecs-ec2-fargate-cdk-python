import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
)

class EcsEc2Stack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image_uri = self.node.try_get_context("image_uri") or os.getenv("IMAGE_URI")
        if not image_uri:
            raise ValueError("Missing image_uri (context) or IMAGE_URI (env var)")

        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        instance_role_arn = self.node.try_get_context("ecs_instance_role_arn") or os.getenv("ECS_INSTANCE_ROLE_ARN")
        if not instance_role_arn:
            raise ValueError("Missing ecs_instance_role_arn (context) or ECS_INSTANCE_ROLE_ARN (env var)")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedTaskExecRole",
            role_arn=exec_role_arn,
            mutable=False,
        )

        instance_role = iam.Role.from_role_arn(
            self, "ImportedInstanceRole",
            role_arn=instance_role_arn,
            mutable=False,
        )

        cluster = ecs.Cluster(self, "Ec2Cluster", vpc=vpc)

        # EC2 Capacity
        asg = autoscaling.AutoScalingGroup(
            self, "EcsAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_capacity=1,
            desired_capacity=1,
            max_capacity=2,
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
            role=instance_role,
        )

        cp = ecs.AsgCapacityProvider(
            self, "AsgCapacityProvider",
            auto_scaling_group=asg,
            enable_managed_scaling=True,
        )
        cluster.add_asg_capacity_provider(cp)

        # Task Definition
        task_def = ecs.Ec2TaskDefinition(self, "TaskDef", execution_role=exec_role)

        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
            memory_limit_mib=256,
            cpu=128,
            # Avoid awslogs to prevent any inline policy grants in restricted envs
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

        # ECS Service (no patterns)
        service = ecs.Ec2Service(
            self, "HelloService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            capacity_provider_strategies=[
                ecs.CapacityProviderStrategy(
                    capacity_provider=cp.capacity_provider_name,
                    weight=1,
                )
            ],
        )

        # ALB
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, allow_all_outbound=True)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")

        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        listener = alb.add_listener("HttpListener", port=80, open=True)

        listener.add_targets(
            "EcsTargets",
            port=80,
            targets=[
                service.load_balancer_target(
                    container_name="hello",
                    container_port=8080,
                )
            ],
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        CfnOutput(self, "Ec2EcsAlbUrl", value=f"http://{alb.load_balancer_dns_name}")
