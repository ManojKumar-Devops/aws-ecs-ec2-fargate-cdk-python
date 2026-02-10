import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
)

class EcsFargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        image_uri: str,
        ecs_exec_role_arn: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not image_uri:
            raise ValueError("Missing image_uri")
        if not ecs_exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedExecRole",
            ecs_exec_role_arn,
            mutable=False,
        )

        cluster = ecs.Cluster(self, "FargateCluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            execution_role=exec_role,
        )

        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

        svc = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "FargateHello",
            cluster=cluster,
            public_load_balancer=True,
            assign_public_ip=True,
            desired_count=1,
            task_definition=task_def,
        )

        svc.target_group.configure_health_check(
            path="/",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )

        CfnOutput(self, "FargateAlbUrl", value=f"http://{svc.load_balancer.load_balancer_dns_name}")
