import os
from constructs import Construct
from aws_cdk import (
    Stack,
    Duration,
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

        cd_role = iam.Role.from_role_arn(self, "ImportedCodeDeployRole", role_arn=codedeploy_role_arn, mutable=False)

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

        instance_sg = ec2.SecurityGroup(self, "InstanceSg", vpc=vpc, allow_all_outbound=True)
        instance_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(80), "HTTP from ALB only")

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum update -y",
            "yum install -y ruby wget",
            "cd /home/ec2-user",
            "wget https://aws-codedeploy-us-east-1.s3.us-east-1.amazonaws.com/latest/install",
            "chmod +x ./install",
            "./install auto",
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",

            "amazon-linux-extras install -y nginx1 || yum install -y nginx",
            "systemctl enable nginx",
            "systemctl start nginx",
        )

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

        listener.add_target_groups("DefaultBlue", target_groups=[blue_tg])
        blue_tg.add_target(asg)

        cd_app = codedeploy.ServerApplication(self, "ServerCdApp")

        dg = codedeploy.ServerDeploymentGroup(
            self, "ServerDg",
            application=cd_app,
            role=cd_role,
            auto_scaling_groups=[asg],
            deployment_config=codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
            load_balancer=codedeploy.LoadBalancer.application(blue_tg, green_tg, listener),
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=True,
            ),
        )

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "CodeDeployApp", value=cd_app.application_name)
        CfnOutput(self, "CodeDeployDg", value=dg.deployment_group_name)
