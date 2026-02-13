#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack
<<<<<<< HEAD

from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack
from stacks.ecs_fargate_stack import EcsFargateStack
# from stacks.ecs_ec2_stack import EcsEc2Stack
from stacks.ecs_fargate_bluegreen_stack import EcsFargateBlueGreenStack
=======
from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack
from stacks.ecs_fargate_stack import EcsFargateStack
from stacks.github_runner_stack import GithubRunnerStack
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)
ecr = EcrStack(app, "EcrStack", env=env)

# Context switches
deploy_ec2_asg = app.node.try_get_context("deploy_ec2_asg") != "false"
<<<<<<< HEAD
deploy_ecs = app.node.try_get_context("deploy_ecs") == "false"
deploy_ecs_bg = app.node.try_get_context("deploy_ecs_bg") == "true"
=======
deploy_ecs = app.node.try_get_context("deploy_ecs") == "true"
deploy_runners = app.node.try_get_context("deploy_runners") == "true"
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")

if deploy_ec2_asg:
    Ec2AlbAsgHelloStack(app, "Ec2AlbAsgHelloStack", vpc=network.vpc, env=env)

<<<<<<< HEAD
if deploy_ecs:
    EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, env=env)
    # EcsEc2Stack(app, "EcsEc2Stack", vpc=network.vpc, env=env)

if deploy_ecs_bg:
    EcsFargateBlueGreenStack(app, "EcsFargateBlueGreenStack", vpc=network.vpc, env=env)    
=======
if deploy_runners:
    instance_profile_name = app.node.try_get_context("runner_instance_profile_name") or os.getenv("RUNNER_INSTANCE_PROFILE_NAME")
    if not instance_profile_name:
        raise ValueError("Missing runner_instance_profile_name (context) or RUNNER_INSTANCE_PROFILE_NAME (env var)")

    GithubRunnerStack(
        app, "GithubRunnerStack",
        vpc=network.vpc,
        owner="ManojKumar-Devops",
        repo="aws-ecs-ec2-fargate-cdk-python",
        instance_profile_name=instance_profile_name,
        github_pat_secret_name="github/pat",
        runner_labels="lab-runner,ec2",
        env=env,
    )


if deploy_ecs:
    EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, env=env)
>>>>>>> 6a1d960 ("ECS Fargate BG Deployment")

app.synth()
