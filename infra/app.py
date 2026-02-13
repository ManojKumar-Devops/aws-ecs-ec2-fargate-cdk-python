#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack
# from stacks.github_runner_stack import GithubRunnerStack
from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack
from stacks.ecs_fargate_stack import EcsFargateStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)
EcrStack(app, "EcrStack", env=env)

deploy_runners = app.node.try_get_context("deploy_runners") == "false"
deploy_ec2_bg = app.node.try_get_context("deploy_ec2_bg") == "false"
deploy_ecs_bg = app.node.try_get_context("deploy_ecs_bg") == "true"

# if deploy_runners:
#     GithubRunnerStack(app, "GithubRunnerStack", vpc=network.vpc, env=env)

if deploy_ec2_bg:
    Ec2AlbAsgHelloStack(app, "Ec2AlbAsgHelloStack", vpc=network.vpc, env=env)

if deploy_ecs_bg:
    EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, env=env)

app.synth()
