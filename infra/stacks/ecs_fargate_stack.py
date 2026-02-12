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

        # Allow infra deploy to succeed even before release workflow pushes real image
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
        test_listener = alb.add_listener(
            "TestListener",
            port=9000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            open=True,
        )


        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTG",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTG",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        # Initial wiring (CodeDeploy will shift traffic between these)
        prod_listener.add_target_groups("ProdTGs", target_groups=[blue_tg])
        test_listener.add_target_groups("TestTGs", target_groups=[green_tg])

        service_sg = ec2.SecurityGroup(self, "ServiceSg", vpc=vpc, allow_all_outbound=True)
        # Fargate tasks receive traffic only from ALB; simplest for lab is allow from VPC CIDR
        service_sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(8080), "HTTP from VPC/ALB")

        service = ecs.FargateService(
            self, "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            security_groups=[service_sg],
            deployment_controller=ecs.DeploymentController(type=ecs.DeploymentControllerType.CODE_DEPLOY),
        )

        # Attach BOTH target groups (required for ECS Blue/Green with CodeDeploy)
        service.attach_to_application_target_group(blue_tg)
        service.attach_to_application_target_group(green_tg)

        cd_app = codedeploy.EcsApplication(self, "EcsCodeDeployApp")

        dg = codedeploy.EcsDeploymentGroup(
            self,
            "EcsDeploymentGroup",
            application=cd_app,
            service=service,
            role=cd_role,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                blue_target_group=blue_tg,
                green_target_group=green_tg,
                listener=prod_listener,
                test_listener=test_listener,
            ),
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=False,
            ),
        )

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "CodeDeployApp", value=cd_app.application_name)
        CfnOutput(self, "CodeDeployDg", value=dg.deployment_group_name)
        CfnOutput(self, "ProdListenerPort", value="80")
        CfnOutput(self, "TestListenerPort", value="9000")
