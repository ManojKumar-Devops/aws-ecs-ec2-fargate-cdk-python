import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_codedeploy as codedeploy,
)

class EcsFargateBlueGreenStack(Stack):
    """
    ECS Fargate Blue/Green using CodeDeploy:
    - ALB Listener :80
    - Two Target Groups: Blue + Green
    - ECS Service with deployment_controller=CODE_DEPLOY
    - CodeDeploy ECS Deployment Group manages traffic shifting
    """

    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Image is only needed for the initial service creation.
        # Real releases will update the task definition via CodeDeploy AppSpec later.
        image_uri = (
            self.node.try_get_context("image_uri")
            or os.getenv("IMAGE_URI")
            or "public.ecr.aws/docker/library/nginx:latest"
        )


        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        codedeploy_role_arn = self.node.try_get_context("codedeploy_role_arn") or os.getenv("CODEDEPLOY_ROLE_ARN")
        if not codedeploy_role_arn:
            raise ValueError("Missing codedeploy_role_arn (context) or CODEDEPLOY_ROLE_ARN (env var)")

        # Optional task role (if you want app to call AWS APIs)
        task_role_arn = self.node.try_get_context("ecs_task_role_arn") or os.getenv("ECS_TASK_ROLE_ARN")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedEcsExecRole",
            role_arn=exec_role_arn,
            mutable=False,
        )

        task_role = None
        if task_role_arn:
            task_role = iam.Role.from_role_arn(
                self, "ImportedEcsTaskRole",
                role_arn=task_role_arn,
                mutable=False,
            )

        codedeploy_role = iam.Role.from_role_arn(
            self, "ImportedCodeDeployRole",
            role_arn=codedeploy_role_arn,
            mutable=False,
        )

        # ----- Networking / ALB
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

        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTg",
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
            self, "GreenTg",
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

        # Default forward to BLUE at start
        listener.add_target_groups("DefaultForward", target_groups=[blue_tg])

        # ----- ECS
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self, "TaskDef",
            cpu=256,
            memory_limit_mib=512,
            execution_role=exec_role,
            task_role=task_role,
        )

        container = task_def.add_container(
            "hello",
            image=ecs.ContainerImage.from_registry(image_uri),
            # avoid awslogs to prevent inline IAM grants in restricted accounts
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8080))

        svc_sg = ec2.SecurityGroup(self, "ServiceSg", vpc=vpc, allow_all_outbound=True)
        svc_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8080), "From ALB to tasks")

        service = ecs.FargateService(
            self, "Service",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            security_groups=[svc_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.CODE_DEPLOY
            ),
        )

        # Attach service to target groups (CodeDeploy will switch traffic between them)
        service.attach_to_application_target_group(blue_tg)
        service.attach_to_application_target_group(green_tg)

        # ----- CodeDeploy (ECS Blue/Green)
        cd_app = codedeploy.EcsApplication(self, "CodeDeployApp")

        dg = codedeploy.EcsDeploymentGroup(
            self, "DeploymentGroup",
            application=cd_app,
            service=service,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                blue_target_group=blue_tg,
                green_target_group=green_tg,
                listener=listener,
            ),
            role=codedeploy_role,
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10PERCENT_5MINUTES,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=True,
            ),
        )

        # ----- Outputs needed by GitHub workflow
        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service_name)
        CfnOutput(self, "CodeDeployAppName", value=cd_app.application_name)
        CfnOutput(self, "CodeDeployGroupName", value=dg.deployment_group_name)
