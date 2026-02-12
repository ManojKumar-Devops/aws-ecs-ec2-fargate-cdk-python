#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack

from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack
from stacks.ecs_fargate_stack import EcsFargateStack
# from stacks.ecs_ec2_stack import EcsEc2Stack
from stacks.ecs_fargate_bluegreen_stack import EcsFargateBlueGreenStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)
ecr = EcrStack(app, "EcrStack", env=env)

# Context switches
deploy_ec2_asg = app.node.try_get_context("deploy_ec2_asg") != "false"
deploy_ecs = app.node.try_get_context("deploy_ecs") == "false"
deploy_ecs_bg = app.node.try_get_context("deploy_ecs_bg") == "true"

if deploy_ec2_asg:
    Ec2AlbAsgHelloStack(app, "Ec2AlbAsgHelloStack", vpc=network.vpc, env=env)

if deploy_ecs:
    EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, env=env)
    # EcsEc2Stack(app, "EcsEc2Stack", vpc=network.vpc, env=env)

if deploy_ecs_bg:
    EcsFargateBlueGreenStack(app, "EcsFargateBlueGreenStack", vpc=network.vpc, env=env)    

app.synth()
