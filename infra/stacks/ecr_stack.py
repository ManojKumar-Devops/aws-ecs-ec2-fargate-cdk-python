from aws_cdk import CfnOutput

CfnOutput(self, "RepoName", value=self.repo.repository_name)
CfnOutput(self, "RepoUri", value=self.repo.repository_uri)
