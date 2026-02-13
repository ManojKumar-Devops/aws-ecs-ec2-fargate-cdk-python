# infra/stacks/ec2_codedeploy_bg_stack.py
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_codedeploy as codedeploy,
    aws_iam as iam,
    aws_autoscaling as autoscaling,
    aws_elasticloadbalancingv2 as elbv2,
)

class Ec2CodeDeployBgStack(Stack):
    """
    Creates CodeDeploy ServerApplication + ServerDeploymentGroup for ASG blue/green.
    Expects the ASG and TG ARNs to already exist (we pass names or import them).
    """
    def __init__(self, scope: Construct, construct_id: str, *,
                 asg_name: str,
                 blue_tg_arn: str,
                 green_tg_arn: str,
                 alb_listener_arn: str = None,
                 codedeploy_role_arn: str = None,
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # If you have a management role for CodeDeploy, import it; otherwise create minimal role
        if codedeploy_role_arn:
            cd_role = iam.Role.from_role_arn(self, "CodeDeployRoleImported", codedeploy_role_arn)
        else:
            cd_role = iam.Role(self, "CodeDeployRole",
                assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSCodeDeployRole"),
                ]
            )

        app = codedeploy.ServerApplication(self, "EC2CodeDeployApp", application_name=f"{self.stack_name}-ec2-app")

        # Import ASG object by name
        asg = autoscaling.AutoScalingGroup.from_auto_scaling_group_name(self, "ImportedASG", asg_name)

        # Note: CDK's ServerDeploymentGroup supports load_balancer construct type
        dg = codedeploy.ServerDeploymentGroup(self, "EC2DG",
            application=app,
            role=cd_role,
            auto_scaling_groups=[asg],
            deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
            # To use Blue/Green server deployments, you will configure the load_balancer and
            # set the blue/green options through the low-level CloudFormation or via the
            # CodeDeploy console. CDK exposes limited convenience API for classic server blue/green.
            load_balancer=codedeploy.LoadBalancer.application(
                elbv2.ApplicationTargetGroup.from_target_group_arn(self, "BlueTGRef", blue_tg_arn)
            ),
        )

        CfnOutput(self, "CodeDeployAppName", value=app.application_name)
        CfnOutput(self, "CodeDeployDeploymentGroup", value=dg.deployment_group_name)
