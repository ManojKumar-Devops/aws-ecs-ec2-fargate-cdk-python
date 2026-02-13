# infra/stacks/ec2_alb_asg_hello_stack.py
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    Duration,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_autoscaling as autoscaling,
)

class Ec2AlbAsgHelloStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        prod_listener = alb.add_listener("ProdListener", port=80, open=True)

        # Create two target groups for blue/green
        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTargetGroup",
            vpc=vpc,
            port=80,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", interval=Duration.seconds(30), healthy_http_codes="200")
        )

        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTargetGroup",
            vpc=vpc,
            port=80,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", interval=Duration.seconds(30), healthy_http_codes="200")
        )

        # Attach blue as default
        prod_listener.add_target_groups("ProdTargetGroups", target_groups=[blue_tg])

        # Security group for instances (allow only from ALB)
        instance_sg = ec2.SecurityGroup(self, "InstanceSg", vpc=vpc, allow_all_outbound=True)
        instance_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(80), "HTTP from ALB only")

        # UserData
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum update -y",
            "amazon-linux-extras install -y nginx1 || yum install -y nginx",
            "cat > /usr/share/nginx/html/index.html <<'EOF'\n"
            "<!doctype html>\n"
            "<html>\n"
            "  <head>\n"
            "    <meta charset=\"utf-8\" />\n"
            "    <title>Hello</title>\n"
            "  </head>\n"
            "  <body>\n"
            "    <h1>Hello World from EC2 + ALB 🚀</h1>\n"
            "  </body>\n"
            "</html>\n"
            "EOF",
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

        # Attach ASG to both TGs — CodeDeploy will control traffic routing
        blue_tg.add_target(asg)
        green_tg.add_target(asg)

        # Basic scaling (CPU)
        asg.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=50)

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
        CfnOutput(self, "BlueTargetGroupArn", value=blue_tg.target_group_arn)
        CfnOutput(self, "GreenTargetGroupArn", value=green_tg.target_group_arn)
        CfnOutput(self, "AsgName", value=asg.auto_scaling_group_name)
