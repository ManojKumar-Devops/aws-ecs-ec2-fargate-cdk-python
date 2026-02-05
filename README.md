# aws-ecs-ec2-fargate-cdk-python

CDK (Python) project that deploys a Hello World container using:
- ECS Fargate + ALB
- ECS on EC2 + ALB
- (Optional) GitHub self-hosted runner on EC2

## Folder structure
- `infra/`  -> CDK app (VPC, ECR, ECS stacks)
- `app/`    -> Hello World container (Flask)
- `.github/`-> GitHub Actions workflow (optional)

## Quick start (local)
1) Build and push Docker image to ECR
2) Deploy with CDK
