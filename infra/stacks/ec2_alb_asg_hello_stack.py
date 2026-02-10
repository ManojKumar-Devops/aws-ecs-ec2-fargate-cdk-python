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

        listener = alb.add_listener("HttpListener", port=80, open=True)

        # Security group for instances (then allow only from ALB)
        instance_sg = ec2.SecurityGroup(self, "InstanceSg", vpc=vpc, allow_all_outbound=True)
        instance_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(80), "HTTP from ALB only")

        # UserData: install nginx and serve Hello World
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "yum update -y",
            "amazon-linux-extras install -y nginx1 || yum install -y nginx",
            "echo 'Hello World from EC2 + ALB 🚀' > /usr/share/nginx/html/index.html",
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

        # Target group via listener
        listener.add_targets(
            "AppTargets",
            port=80,
            targets=[asg],
            health_check=elbv2.HealthCheck(
                path="/",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
            ),
        )

        # Basic scaling (CPU)
        asg.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=50)

        CfnOutput(self, "AlbUrl", value=f"http://{alb.load_balancer_dns_name}")
