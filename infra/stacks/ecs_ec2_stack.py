from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_ecr as ecr,
)

class EcsEc2Stack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        repository: ecr.IRepository,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(self, "Ec2Cluster", vpc=vpc)

        # ECS EC2 instances in PUBLIC subnet (no NAT)
        asg = autoscaling.AutoScalingGroup(
            self, "EcsAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_capacity=1,
            desired_capacity=1,
            max_capacity=2,
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(),
        )

        cp = ecs.AsgCapacityProvider(
            self, "AsgCapacityProvider",
            auto_scaling_group=asg,
            enable_managed_scaling=True,
        )
        cluster.add_asg_capacity_provider(cp)

        # ---- IMPORTANT PART: EC2 task definition with memory set ----
        task_def = ecs.Ec2TaskDefinition(self, "TaskDef")

        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_ecr_repository(repository, tag="latest"),
            memory_limit_mib=256,   # ✅ required for EC2 tasks
            cpu=128,
        )
        container.add_port_mappings(
            ecs.PortMapping(container_port=8080)
        )

        svc = ecs_patterns.ApplicationLoadBalancedEc2Service(
            self, "Ec2Hello",
            cluster=cluster,
            public_load_balancer=True,
            desired_count=1,
            task_definition=task_def,   # ✅ use our task definition
        )

        svc.target_group.configure_health_check(
            path="/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )

        scaling = svc.service.auto_scale_task_count(min_capacity=1, max_capacity=3)
        scaling.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=50)

        CfnOutput(self, "Ec2URL", value=f"http://{svc.load_balancer.load_balancer_dns_name}")
