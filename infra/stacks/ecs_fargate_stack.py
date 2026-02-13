import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
<<<<<<< HEAD
    aws_ecs_patterns as ecs_patterns,
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

        # Image URI is injected by GitHub Actions (or via -c image_uri=...)
=======
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
)

class EcsFargateStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Context toggles
        enable_bg = self.node.try_get_context("enable_bg") == "true"
        enable_test_listener = self.node.try_get_context("enable_test_listener") == "true"

        # Image URI injected by workflow (context or env)
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
        image_uri = self.node.try_get_context("image_uri") or os.getenv("IMAGE_URI")
        if not image_uri:
            raise ValueError("Missing image_uri (context) or IMAGE_URI (env var)")

<<<<<<< HEAD
        # Pre-created execution role (manual) to avoid iam:PutRolePolicy
=======
        # Keep your pattern: imported exec role to avoid inline policy restrictions in some labs
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedFargateExecRole",
            role_arn=exec_role_arn,
<<<<<<< HEAD
            mutable=False,  # IMPORTANT: prevents CDK from attaching inline policies
=======
            mutable=False,
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
        )

        cluster = ecs.Cluster(self, "FargateCluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            execution_role=exec_role,
        )

<<<<<<< HEAD
        # IMPORTANT: avoid awslogs here (can trigger grants/inline policies in some environments)
=======
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

<<<<<<< HEAD
        svc = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "FargateHello",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            public_load_balancer=True,
            assign_public_ip=True,
=======
        # ALB
        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            vpc=vpc,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        prod_listener = alb.add_listener("ProdHttp", port=80, open=True)

        test_listener = None
        if enable_test_listener:
            test_listener = alb.add_listener("TestHttp", port=9002, open=True)

        # Blue + Green target groups (IP target type for Fargate)
        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTargetGroup",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTargetGroup",
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

<<<<<<< HEAD
        CfnOutput(self, "FargateAlbUrl", value=f"http://{svc.load_balancer.load_balancer_dns_name}")
=======
        # Prod listener defaults to BLUE
        prod_listener.add_target_groups("ProdTraffic", target_groups=[blue_tg])

        if enable_test_listener and test_listener is not None:
            # Test listener defaults to GREEN (so you can validate new tasks before cutover)
            test_listener.add_target_groups("TestTraffic", target_groups=[green_tg])

        # ECS Service
        svc = ecs.FargateService(
            self, "FargateSvc",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.CODE_DEPLOY if enable_bg else ecs.DeploymentControllerType.ECS
            ),
        )

        # Attach service to BLUE TG (CodeDeploy will shift between TGs)
        svc.attach_to_application_target_group(blue_tg)

        # CodeDeploy ECS Blue/Green
        if enable_bg:
            cd_app = codedeploy.EcsApplication(self, "EcsCodeDeployApp")

            codedeploy.EcsDeploymentGroup(
                self, "EcsBlueGreenDG",
                application=cd_app,
                service=svc,
                blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                    blue_target_group=blue_tg,
                    green_target_group=green_tg,
                    listener=prod_listener,
                    test_listener=test_listener if enable_test_listener else None,
                ),
                # Safe default: all at once. (You can switch to canary/linear later)
                deployment_config=codedeploy.EcsDeploymentConfig.ALL_AT_ONCE,
            )

        CfnOutput(self, "FargateAlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        if enable_test_listener and test_listener is not None:
            CfnOutput(self, "FargateTestUrl", value=f"http://{alb.load_balancer_dns_name}:9002")
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")
