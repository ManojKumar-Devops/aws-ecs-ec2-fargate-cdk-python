from constructs import Construct
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_ecr as ecr,
)

class EcrStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repo = ecr.Repository(
            self, "HelloRepo",
            repository_name="hello-world-lab",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            image_scan_on_push=True,
        )
