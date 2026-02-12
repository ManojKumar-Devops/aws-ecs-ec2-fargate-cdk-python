import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
)

class EcsFargateStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image_uri = (
            self.node.try_get_context("image_uri")
            or os.getenv("IMAGE_URI")
            or "public.ecr.aws/nginx/nginx:latest"
)

        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        codedeploy_role_arn = self.node.try_get_context("codedeploy_role_arn") or os.getenv("CODEDEPLOY_ROLE_ARN")
        if not codedeploy_role_arn:
            raise ValueError("Missing codedeploy_role_arn (context) or CODEDEPLOY_ROLE_ARN (env var)")

        exec_role = iam.Role.from_role_arn(self, "ImportedExecRole", role_arn=exec_role_arn, mutable=False)
        cd_role = iam.Role.from_role_arn(self, "ImportedCodeDeployRole", role_arn=codedeploy_role_arn, mutable=False)

        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            execution_role=exec_role,
        )

        container = task_def.add_container(
            "App",
            image=ecs.ContainerImage.from_registry(image_uri),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

        alb = elbv2.ApplicationLoadBalancer(self, "Alb", vpc=vpc, internet_facing=True)
        prod_listener = alb.add_listener("ProdListener", port=80, open=True)
        test_listener = alb.add_listener("TestListener", port=9000, open=True)

        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTG",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200"),
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTG",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200"),
        )

        prod_listener.add_target_groups("ProdTG", target_groups=[blue_tg])
        test_listener.add_target_groups("TestTG", target_groups=[green_tg])

        service_sg = ec2.SecurityGroup(self, "ServiceSg", vpc=vpc, allow_all_outbound=True)
        service_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(8080), "allow ALB health checks")

        service = ecs.FargateService(
            self, "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            security_groups=[service_sg],
            deployment_controller=ecs.DeploymentController(type=ecs.DeploymentControllerType.CODE_DEPLOY),
        )

        # Attach both TGs (CodeDeploy shifts traffic)
        service.attach_to_application_target_group(blue_tg)
        service.attach_to_application_target_group(green_tg)

        cd_app = codedeploy.EcsApplication(self, "EcsCdApp")

        dg = codedeploy.EcsDeploymentGroup(
            self, "EcsDg",
            application=cd_app,
            service=se
