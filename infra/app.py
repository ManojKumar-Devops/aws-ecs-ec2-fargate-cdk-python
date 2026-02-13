#!/usr/bin/env python3
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.ecr_stack import EcrStack

from stacks.ec2_alb_asg_hello_stack import Ec2AlbAsgHelloStack
from stacks.ec2_codedeploy_bg_stack import Ec2CodeDeployBgStack

from stacks.ecs_fargate_stack import EcsFargateStack
from stacks.ecs_codedeploy_bg_stack import EcsCodeDeployBgStack

from stacks.github_runners_stack import GithubRunnersStack

app = cdk.App()

env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=os.getenv("CDK_DEFAULT_REGION"),
)

network = NetworkStack(app, "NetworkStack", env=env)
ecr = EcrStack(app, "EcrStack", env=env)

# Context switches (default: false unless enabled)
deploy_runners = app.node.try_get_context("deploy_runners") == "true"
deploy_ec2_bg = app.node.try_get_context("deploy_ec2_bg") == "true"
deploy_ecs_bg = app.node.try_get_context("deploy_ecs_bg") == "true"

# ---- Self-hosted runners (ASG mixed spot/on-demand) ----
if deploy_runners:
    GithubRunnersStack(
        app,
        "GithubRunnersStack",
        vpc=network.vpc,
        # IMPORTANT: fill these (or use repo variables + cdk context)
        github_owner_or_repo=app.node.try_get_context("github_owner_or_repo") or "REPLACE_ME_OWNER_OR_REPO",
        github_runner_token_ssm_param=app.node.try_get_context("github_runner_token_ssm_param") or "/github/runner/token",
        runner_labels=app.node.try_get_context("runner_labels") or "lab-runner",
        env=env,
    )

# ---- EC2 ALB + ASG stack (blue/green target groups) + CodeDeploy (Server) ----
if deploy_ec2_bg:
    ec2_stack = Ec2AlbAsgHelloStack(app, "Ec2AlbAsgHelloStack", vpc=network.vpc, env=env)

    Ec2CodeDeployBgStack(
        app,
        "Ec2CodeDeployBgStack",
        # wire outputs from ec2 stack
        asg_name=ec2_stack.asg.auto_scaling_group_name,
        blue_tg_arn=ec2_stack.blue_tg.target_group_arn,
        green_tg_arn=ec2_stack.green_tg.target_group_arn,
        prod_listener_arn=ec2_stack.prod_listener.listener_arn,
        # OPTIONAL: set role ARN via context if your environment blocks IAM creation
        codedeploy_role_arn=app.node.try_get_context("codedeploy_server_role_arn"),
        env=env,
    )

# ---- ECS Fargate stack (blue/green) + CodeDeploy (ECS) ----
if deploy_ecs_bg:
    ecs_stack = EcsFargateStack(app, "EcsFargateStack", vpc=network.vpc, env=env)

    EcsCodeDeployBgStack(
        app,
        "EcsCodeDeployBgStack",
        ecs_service=ecs_stack.service,
        prod_listener=ecs_stack.prod_listener,
        test_listener=ecs_stack.test_listener,
        blue_tg=ecs_stack.blue_tg,
        green_tg=ecs_stack.green_tg,
        # OPTIONAL: set role ARN via context if your environment blocks IAM creation
        codedeploy_role_arn=app.node.try_get_context("codedeploy_ecs_role_arn"),
        env=env,
    )

app.synth()
