import os
from constructs import Construct
from aws_cdk import RemovalPolicy
from aws_cdk import (
    Stack,
    CfnParameter,
    Duration,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
)

class GithubRunnerEcsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        github_owner = self.node.try_get_context("github_owner") or os.getenv("REPO_OWNER")
        github_repo  = self.node.try_get_context("github_repo") or os.getenv("REPO_NAME")
        runner_labels = self.node.try_get_context("runner_labels") or "lab-runner"
        runner_name   = self.node.try_get_context("runner_name") or "fargate-runner"

        if not github_owner or not github_repo:
            raise ValueError("Missing github_owner/github_repo (context)")

        # PAT is passed as a CFN parameter (NoEcho hides it in console)
        runner_pat = CfnParameter(
            self, "RunnerPat",
            type="String",
            no_echo=True,
            description="GitHub PAT used by runner container (NoEcho)",
        )

        # Import your pre-created execution role (avoid IAM inline policy operations)
        exec_role_arn = self.node.try_get_context("ecs_exec_role_arn") or os.getenv("ECS_EXEC_ROLE_ARN")
        if not exec_role_arn:
            raise ValueError("Missing ecs_exec_role_arn (context) or ECS_EXEC_ROLE_ARN (env var)")

        exec_role = iam.Role.from_role_arn(
            self, "ImportedFargateExecRole",
            role_arn=exec_role_arn,
            mutable=False,
        )

        task_role_arn = self.node.try_get_context("runner_task_role_arn") or os.getenv("RUNNER_TASK_ROLE_ARN")

        if not task_role_arn:
            raise ValueError("Missing runner_task_role_arn (context)")

        task_role = iam.Role.from_role_arn(
            self,
            "ImportedRunnerTaskRole",
            role_arn=task_role_arn,
            mutable=False,
        )

        cluster = ecs.Cluster(self, "RunnerCluster", vpc=vpc)

        task_def = ecs.FargateTaskDefinition(
            self,
            "RunnerTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            execution_role=exec_role,
            task_role=task_role,   # 👈 important
        )

        container = task_def.add_container(
            "Runner",
            image=ecs.ContainerImage.from_registry("myoung34/github-runner:latest"),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="runner",
                environment={ ... },
            ),
            environment={
                # For repo runner, REPO_URL must be full repo URL :contentReference[oaicite:2]{index=2}
                "REPO_URL": f"https://github.com/{github_owner}/{github_repo}",
                # ACCESS_TOKEN is PAT used to generate RUNNER_TOKEN automatically :contentReference[oaicite:3]{index=3}
                "ACCESS_TOKEN": runner_pat.value_as_string,
                "RUNNER_NAME": runner_name,
                # RUNNER_LABELS is supported (LABELS fallback) :contentReference[oaicite:4]{index=4}
                "RUNNER_LABELS": runner_labels,

                # Nice-to-haves:
                "RUNNER_WORKDIR": "/tmp/runner",
                "EPHEMERAL": "false",
                "DISABLE_AUTO_UPDATE": "true",
            },
        )

        sg = ec2.SecurityGroup(self, "RunnerServiceSg", vpc=vpc, allow_all_outbound=True)

        service = ecs.FargateService(
            self, "RunnerService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,
            security_groups=[sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            health_check_grace_period=Duration.seconds(60),
        )
