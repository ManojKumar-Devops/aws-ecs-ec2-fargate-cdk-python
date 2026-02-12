import os
from constructs import Construct
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)

class GithubRunnerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.IVpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ===== Required context =====
        # Store GitHub PAT in Secrets Manager (SecretString either plain token OR {"token":"..."} )
        secret_arn = self.node.try_get_context("github_runner_pat_secret_arn")
        owner = self.node.try_get_context("github_owner")      # ManojKumar-Devops
        repo = self.node.try_get_context("github_repo")        # aws-ecs-ec2-fargate-cdk-python
        labels = self.node.try_get_context("runner_labels") or "aws,cdk"

        if not secret_arn:
            raise ValueError("Missing context: github_runner_pat_secret_arn")
        if not owner or not repo:
            raise ValueError("Missing context: github_owner and github_repo")

        secret = secretsmanager.Secret.from_secret_complete_arn(self, "RunnerPat", secret_arn)

        # ===== Security Group =====
        sg = ec2.SecurityGroup(self, "RunnerSg", vpc=vpc, allow_all_outbound=True)

        # ===== IAM role for EC2 runner =====
        role = iam.Role(self, "RunnerRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"))
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"))
        secret.grant_read(role)

        # ===== User data =====
        ud = ec2.UserData.for_linux()
        ud.add_commands(
            "set -euxo pipefail",
            "yum update -y",
            "yum install -y jq curl tar gzip",
            "cd /opt",
            "RUNNER_VERSION=2.317.0",
            "curl -L -o actions-runner.tar.gz https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz",
            "mkdir -p actions-runner",
            "tar xzf actions-runner.tar.gz -C actions-runner",
            "cd /opt/actions-runner",

            # fetch PAT from Secrets Manager (plain string OR JSON)
            f"PAT_RAW=$(aws secretsmanager get-secret-value --secret-id {secret.secret_arn} --query SecretString --output text)",
            "if echo \"$PAT_RAW\" | jq -e . >/dev/null 2>&1; then PAT=$(echo \"$PAT_RAW\" | jq -r '.token'); else PAT=\"$PAT_RAW\"; fi",
            "if [ -z \"$PAT\" ] || [ \"$PAT\" = \"null\" ]; then echo 'Missing PAT'; exit 1; fi",

            # request a runner registration token
            f"OWNER='{owner}'",
            f"REPO='{repo}'",
            "REG_TOKEN=$(curl -s -X POST -H \"Authorization: token $PAT\" -H \"Accept: application/vnd.github+json\" "
            "https://api.github.com/repos/${OWNER}/${REPO}/actions/runners/registration-token | jq -r .token)",
            "if [ -z \"$REG_TOKEN\" ] || [ \"$REG_TOKEN\" = \"null\" ]; then echo 'Failed to get runner reg token'; exit 1; fi",

            # configure runner (ephemeral: 1 job then exit)
            f"./config.sh --unattended --url https://github.com/{owner}/{repo} "
            f"--token \"$REG_TOKEN\" --labels \"{labels}\" --ephemeral --name \"runner-$(hostname)\"",

            "./svc.sh install",
            "./svc.sh start",
        )

        # ===== AutoScalingGroup =====
        asg = autoscaling.AutoScalingGroup(
            self, "RunnerAsg",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t3.large"),
            machine_image=ec2.AmazonLinuxImage(generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2),
            min_capacity=1,       # start with 1 so it registers; later set 0 and autoscale
            desired_capacity=1,
            max_capacity=3,
            security_group=sg,
            role=role,
            user_data=ud,
        )

        CfnOutput(self, "RunnerAsgName", value=asg.auto_scaling_group_name)
