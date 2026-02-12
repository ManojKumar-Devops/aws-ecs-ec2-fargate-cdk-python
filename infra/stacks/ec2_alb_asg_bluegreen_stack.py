from constructs import Construct
from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_autoscaling as autoscaling,
    aws_codedeploy as codedeploy,
)

class Ec2AlbAsgBlueGreenStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ALB SG
        alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=vpc, allow_all_outbound=True)
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")

        # Instance SG (only from ALB)
        instance_sg = ec2.SecurityGroup(self, "InstanceSg", vpc=vpc, allow_all_outbound=True)
        instance_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(80), "HTTP from ALB")

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
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200", interval=Duration.seconds(30)),
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTg",
            vpc=vpc,
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", healthy_http_codes="200", interval=Duration.seconds(30)),
        )

        # Default listener goes to BLUE initially
        listener.add_target_groups("DefaultBlue", target_groups=[blue_tg])

        # User data: install CodeDeploy agent + nginx (demo)
        ud = ec2.UserData.for_linux()
        ud.add_commands(
            "yum update -y",
            "amazon-linux-extras install -y nginx1 || yum install -y nginx",
            "systemctl enable nginx",
            "systemctl start nginx",
            # CodeDeploy agent (AL2)
            "yum install -y ruby wget",
            "cd /home/ec2-user",
            "REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)",
            "wget https://aws-codedeploy-$REGION.s3.$REGION.amazonaws.com/latest/install",
            "chmod +x ./install",
            "./install auto",
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",
        )

        # BLUE ASG
        blue_asg = autoscaling.AutoScalingGroup(
            self, "BlueAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            min_capacity=1,
            desired_capacity=1,
            max_capacity=2,
            security_group=instance_sg,
            user_data=ud,
        )

        # GREEN ASG (starts at 0; CodeDeploy will scale/replace)
        green_asg = autoscaling.AutoScalingGroup(
            self, "GreenAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            min_capacity=0,
            desired_capacity=0,
            max_capacity=2,
            security_group=instance_sg,
            user_data=ud,
        )

        blue_tg.add_target(blue_asg)
        green_tg.add_target(green_asg)

        # CodeDeploy App + Deployment Group (Blue/Green)
        app = codedeploy.ServerApplication(self, "CodeDeployApp")

        dg = codedeploy.ServerDeploymentGroup(
            self, "DeploymentGroup",
            application=app,
            auto_scaling_groups=[blue_asg, green_asg],
            load_balancer=codedeploy.LoadBalancer.application(blue_tg, green_tg, listener),
            deployment_config=codedeploy.ServerDeploymentConfig.ALL_AT_ONCE,
            # You can also use CANARY/LINEAR if you prefer
            # deployment_config=codedeploy.ServerDeploymentConfig.LINEAR_10_PERCENT_EVERY_1_MINUTE,

            # Blue/Green specific behavior:
            # Terminate old fleet after success
            # (CDK handles the underlying CFN params)
            blue_green_deployment_config=codedeploy.BlueGreenDeploymentConfig(
                listener=listener,
                blue_target_group=blue_tg,
                green_target_group=green_tg,
                termination_wait_time=Duration.minutes(5),
            ),
        )

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "CodeDeployAppName", value=app.application_name)
        CfnOutput(self, "CodeDeployDeploymentGroupName", value=dg.deployment_group_name)
