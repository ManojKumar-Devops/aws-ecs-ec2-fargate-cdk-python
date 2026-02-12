#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack
from stacks.ec2_alb_asg_bluegreen_stack import Ec2AlbAsgBlueGreenStack
from stacks.github_runners_stack import GithubRunnersStack

# Playground deployable stack
from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack

# Real AWS stacks (need IAM perms)
from stacks.ecs_fargate_stack import EcsFargateStack
from stacks.ecs_ec2_stack import EcsEc2Stack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)
ecr = EcrStack(app, "EcrStack", env=env)

# Always deploy playground-friendly stack unless disabled
deploy_ec2_asg = app.node.try_get_context("deploy_ec2_asg") != "false"
deploy_ecs = app.node.try_get_context("deploy_ecs") == "true"
deploy_ec2_bg = app.node.try_get_context("deploy_ec2_bg") == "true"
deploy_runners = app.node.try_get_context("deploy_runners") == "true"

if deploy_ec2_asg:
    Ec2AlbAsgHelloStack(app, "Ec2AlbAsgHelloStack", vpc=network.vpc, env=env)

if deploy_ec2_bg:
    Ec2AlbAsgBlueGreenStack(app, "Ec2AlbAsgBlueGreenStack", vpc=network.vpc, env=env)

if deploy_runners:
    GithubRunnersStack(app, "GithubRunnersStack", vpc=network.vpc, env=env)    

# ECS stacks: enable only when you have full IAM permissions in real AWS
if deploy_ecs:
    EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, repository=ecr.repo, env=env)
    EcsEc2Stack(app, "EcsEc2Stack", vpc=network.vpc, repository=ecr.repo, env=env)

app.synth()
