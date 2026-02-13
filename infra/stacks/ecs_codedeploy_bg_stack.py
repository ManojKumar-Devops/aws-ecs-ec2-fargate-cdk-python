# infra/stacks/ecs_codedeploy_bg_stack.py
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_codedeploy as codedeploy,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
)

class EcsCodeDeployBgStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 ecs_service: ecs.FargateService = None,
                 prod_listener: elbv2.ApplicationListener = None,
                 test_listener: elbv2.ApplicationListener = None,
                 blue_tg: elbv2.ApplicationTargetGroup = None,
                 green_tg: elbv2.ApplicationTargetGroup = None,
                 codedeploy_role_arn: str = None,
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Import or create CodeDeploy role
        if codedeploy_role_arn:
            cd_role = iam.Role.from_role_arn(self, "CdRoleImported", codedeploy_role_arn)
        else:
            cd_role = iam.Role(self, "CdRole",
                assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSCodeDeployRoleForECS")
                ]
            )

        app = codedeploy.EcsApplication(self, "EcsCodeDeployApp", application_name=f"{self.stack_name}-ecs-app")

        # Create a deployment group that points at the ECS service
        dg = codedeploy.EcsDeploymentGroup(self, "EcsDeploymentGroup",
            application=app,
            service=ecs_service,
            deployment_config=codedeploy.EcsDeploymentConfig.ALL_AT_ONCE,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                blue_target_group=blue_tg,
                green_target_group=green_tg,
                listener=prod_listener,
                test_listener=test_listener
            ),
            role=cd_role,
        )

        CfnOutput(self, "EcsCodedeployApp", value=app.application_name)
        CfnOutput(self, "EcsCodedeployDeploymentGroup", value=dg.deployment_group_name)
