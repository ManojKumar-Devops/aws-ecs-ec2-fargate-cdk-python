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
        image_uri: str,
        ecs_instance_role_arn: str,
        ecs_exec_role_arn: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------
        # ECS Cluster (classic EC2)
        # -------------------------------
        cluster = ecs.Cluster(self, "Ec2Cluster", vpc=vpc)

        # -------------------------------
        # IMPORT EXISTING ROLES (NO CREATE)
        # -------------------------------
        instance_role = iam.Role.from_role_arn(
            self, "EcsInstanceRole",
            ecs_instance_role_arn,
            mutable=False,
        )

        exec_role = iam.Role.from_role_arn(
            self, "EcsExecRole",
            ecs_exec_role_arn,
            mutable=False,
        )

        # -------------------------------
        # Auto Scaling Group (EC2 capacity)
        # -------------------------------
        asg = autoscaling.AutoScalingGroup(
            self, "EcsAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
            min_capacity=1,
            desired_capacity=1,
            max_capacity=2,
            role=instance_role,
        )

        # 🔴 IMPORTANT: classic ECS registration (NO capacity provider)
        cluster.add_auto_scaling_group(asg)

        # -------------------------------
        # Task Definition (EC2)
        # -------------------------------
        task_def = ecs.Ec2TaskDefinition(
            self, "TaskDef",
            execution_role=exec_role,
        )

        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
            memory_limit_mib=256,
            cpu=128,
        )
        container.add_port_mappings(
            ecs.PortMapping(container_port=8080)
        )

        # -------------------------------
        # ECS Service (EC2)
        # -------------------------------
        service = ecs.Ec2Service(
            self, "HelloService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
        )

        # -------------------------------
        # Application Load Balancer
        # -------------------------------
        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            vpc=vpc,
            internet_facing=True,
        )

        listener = alb.add_listener("HttpListener", port=80, open=True)

        tg = listener.add_targets(
            "EcsTargets",
            port=8080,
            targets=[service],
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        # -------------------------------
        # Output
        # -------------------------------
        CfnOutput(
            self,
            "Ec2EcsAlbUrl",
            value=f"http://{alb.load_balancer_dns_name}",
        )
