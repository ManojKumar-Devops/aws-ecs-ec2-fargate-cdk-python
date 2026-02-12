import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_autoscaling as autoscaling,
    aws_codedeploy as codedeploy,
    aws_iam as iam,
)

class Ec2AlbAsgHelloStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        codedeploy_role_arn = self.node.try_get_context("codedeploy_role_arn") or os.getenv("CODEDEPLOY_ROLE_ARN")
        if not codedeploy_role_arn:
            raise ValueError("Missing codedeploy_role_arn (context) or CODEDEPLOY_ROLE_ARN (env var)")

        cd_role = iam.Role.from_role_arn(
            self,
            "ImportedCodeDeployRole",
            role_arn=codedeploy_role_arn,
            mutable=False
        )

        # Security group for ALB
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, allow_all_outbound=True)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")

        # ALB
        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )
        listener = alb.add_listener("HttpListener", port=80, open=True)

        # Security group for instances (allow only from ALB)
        instance_sg = ec2.SecurityGroup(self, "InstanceSg", vpc=vpc, allow_all_outbound=True)
        instance_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(80), "HTTP from ALB only")

        # UserData: install CodeDeploy agent + nginx
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y ruby wget",

            # Region-safe CodeDeploy agent install
            "REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)",
            "cd /home/ec2-user",
            "wget \"https://aws-codedeploy-${REGION}.s3.${REGION}.amazonaws.com/latest/install\"",
            "chmod +x ./install",
            "./install auto",
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",

            "amazon-linux-extras install -y nginx1 || yum install -y nginx",
            "systemctl enable nginx",
            "systemctl start nginx",
        )

        # ASG (this is the "blue" ASG initially; CodeDeploy will copy it to create green fleet)
        asg = autoscaling.AutoScalingGroup(
            self, "Asg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            min_capacity=1,
            desired_capacity=1,
            max_capacity=2,
            security_group=instance_sg,
            user_data=user_data,
        )

        # Two target groups for Blue/Green
        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTG",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200"),
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTG",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200"),
        )

        # Start with Blue in prod listener
        listener.add_target_groups("DefaultBlue", target_groups=[blue_tg])
        blue_tg.add_target(asg)

        # ---- CodeDeploy Blue/Green (L1 resources for max CDK compatibility) ----
        app_name = f"{self.stack_name}-ServerApp"
        dg_name = f"{self.stack_name}-ServerDg"

        codedeploy.CfnApplication(
            self,
            "ServerCdApp",
            compute_platform="Server",
            application_name=app_name,
        )

        codedeploy.CfnDeploymentGroup(
            self,
            "ServerDg",
            application_name=app_name,
            deployment_group_name=dg_name,
            service_role_arn=cd_role.role_arn,
            auto_scaling_groups=[asg.auto_scaling_group_name],
            deployment_style=codedeploy.CfnDeploymentGroup.DeploymentStyleProperty(
                deployment_type="BLUE_GREEN",
                deployment_option="WITH_TRAFFIC_CONTROL",
            ),
            blue_green_deployment_configuration=codedeploy.CfnDeploymentGroup.BlueGreenDeploymentConfigurationProperty(
                green_fleet_provisioning_option=codedeploy.CfnDeploymentGroup.GreenFleetProvisioningOptionProperty(
                    action="COPY_AUTO_SCALING_GROUP",
                ),
                terminate_blue_instances_on_deployment_success=codedeploy.CfnDeploymentGroup.BlueInstanceTerminationOptionProperty(
                    action="TERMINATE",
                    termination_wait_time_in_minutes=5,
                ),
                deployment_ready_option=codedeploy.CfnDeploymentGroup.DeploymentReadyOptionProperty(
                    action_on_timeout="CONTINUE_DEPLOYMENT",
                    wait_time_in_minutes=0,
                ),
            ),
            load_balancer_info=codedeploy.CfnDeploymentGroup.LoadBalancerInfoProperty(
                target_group_pair_info_list=[
                    codedeploy.CfnDeploymentGroup.TargetGroupPairInfoProperty(
                        prod_traffic_route=codedeploy.CfnDeploymentGroup.TrafficRouteProperty(
                            listener_arns=[listener.listener_arn]
                        ),
                        target_groups=[
                            codedeploy.CfnDeploymentGroup.TargetGroupInfoProperty(
                                name=blue_tg.target_group_name
                            ),
                            codedeploy.CfnDeploymentGroup.TargetGroupInfoProperty(
                                name=green_tg.target_group_name
                            ),
                        ],
                    )
                ]
            ),
        )

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "CodeDeployApp", value=app_name)
        CfnOutput(self, "CodeDeployDg", value=dg_name)
