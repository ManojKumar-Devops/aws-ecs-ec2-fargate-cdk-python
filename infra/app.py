#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack
from stacks.ecs_fargate_stack import EcsFargateStack
from stacks.ecs_ec2_stack import EcsEc2Stack


app = cdk.App()

# Pick one of the allowed playground regions (us-east-1 / us-east-2 / us-west-2)
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)

ecr = EcrStack(app, "EcrStack", env=env)

# Fargate path (simplest)
fargate = EcsFargateStack(
    app,
    "EcsFargateStack",
    vpc=network.vpc,
    repository=ecr.repo,
    env=env,
)

# EC2 path (capacity provider + mixed instances)
ec2 = EcsEc2Stack(
    app,
    "EcsEc2Stack",
    vpc=network.vpc,
    repository=ecr.repo,
    env=env,
)

# Optional: self-hosted GitHub runner on EC2
# (Use only if your org allows runner registration and outbound to GitHub is permitted)
# runner = GithubRunnerStack(app, "GithubRunnerStack", vpc=network.vpc, env=env)

app.synth()

