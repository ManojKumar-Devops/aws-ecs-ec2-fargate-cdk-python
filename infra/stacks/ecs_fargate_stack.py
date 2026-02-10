from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_ecs_patterns as ecs_patterns,
)

class EcsFargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        repository: ecr.IRepository,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(self, "FargateCluster", vpc=vpc)

        svc = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "FargateHello",
            cluster=cluster,
            public_load_balancer=True,
            assign_public_ip=True,   # if you have NAT, you can set False + private subnets
            desired_count=1,
            cpu=256,
            memory_limit_mib=512,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                container_name="hello",
                image=ecs.ContainerImage.from_ecr_repository(repository, tag="latest"),
                container_port=8080,
            ),
        )

        svc.target_group.configure_health_check(
            path="/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )

        scaling = svc.service.auto_scale_task_count(min_capacity=1, max_capacity=3)
        scaling.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=50)

        CfnOutput(self, "FargateAlbUrl", value=f"http://{svc.load_balancer.load_balancer_dns_name}")
