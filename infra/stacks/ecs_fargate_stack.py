import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_codedeploy as codedeploy,
    aws_iam as iam,
)

class EcsFargateStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Image URI injected by pipeline (optional for first deploy; CodeDeploy will update later)
        image_uri = self.node.try_get_context("image_uri") or os.getenv("IMAGE_URI")
        if not image_uri:
            # placeholder so CDK synth/deploy can run before CI builds/pushes the image
            image_uri = "public.ecr.aws/nginx/nginx:latest"


        # Pre-created execution role (manual) to avoid iam:PutRolePolicy
        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedFargateExecRole",
            role_arn=exec_role_arn,
            mutable=False,
        )

        cluster = ecs.Cluster(self, "FargateCluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            execution_role=exec_role,
        )

        # Keep logging minimal to avoid extra grants in restricted envs
        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

        # -------- ALB (explicit, no patterns) --------
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, allow_all_outbound=True)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")
        # Test listener port (used by CodeDeploy validation before shifting traffic)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(9002), "Test listener")

        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        prod_listener = alb.add_listener("ProdListener", port=80, open=True)
        test_listener = alb.add_listener("TestListener", port=9002, open=True)

        # Blue/Green target groups (Fargate => TargetType.IP)
        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTg",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTg",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        # Default routing: PROD -> BLUE, TEST -> GREEN
        prod_listener.add_target_groups("ProdDefaultBlue", target_groups=[blue_tg])
        test_listener.add_target_groups("TestDefaultGreen", target_groups=[green_tg])

        # -------- ECS Service (CodeDeploy controller) --------
        service_sg = ec2.SecurityGroup(self, "ServiceSg", vpc=vpc, allow_all_outbound=True)
        # allow ALB to reach service on 8080
        service_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "ALB to ECS tasks")

        service = ecs.FargateService(
            self, "FargateHelloService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,  # You are using public subnets in your network stack
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[service_sg],
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.CODE_DEPLOY
            ),
        )

        # Attach the service to BLUE TG initially
        service.attach_to_application_target_group(blue_tg)

        # -------- CodeDeploy ECS Blue/Green --------
        cd_app = codedeploy.EcsApplication(self, "CodeDeployEcsApp")

        dg = codedeploy.EcsDeploymentGroup(
            self, "CodeDeployEcsDg",
            application=cd_app,
            service=service,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                blue_target_group=blue_tg,
                green_target_group=green_tg,
                listener=prod_listener,
                test_listener=test_listener,
            ),
            # Pick one:
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10PERCENT_5MINUTES,
            # deployment_config=codedeploy.EcsDeploymentConfig.LINEAR_10PERCENT_EVERY_1MINUTE,
            # deployment_config=codedeploy.EcsDeploymentConfig.ALL_AT_ONCE,
        )

        # Outputs for pipeline
        CfnOutput(self, "AlbDns", value=alb.load_balancer_dns_name)
        CfnOutput(self, "ProdUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "TestUrl", value=f"http://{alb.load_balancer_dns_name}:9002")

        CfnOutput(self, "EcsClusterName", value=cluster.cluster_name)
        CfnOutput(self, "EcsServiceName", value=service.service_name)

        CfnOutput(self, "CodeDeployAppName", value=cd_app.application_name)
        CfnOutput(self, "CodeDeployDeploymentGroupName", value=dg.deployment_group_name)
