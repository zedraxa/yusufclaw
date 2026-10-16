"""ECS Fargate Heavy Jobs construct for YusufClaw personal AI agent."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra_py.constructs.bedrock_iam import BedrockPermissions
from infra_py.constructs.database import YusufClawDatabase
from infra_py.constructs.storage import YusufClawStorage


class YusufClawHeavyJobs(Construct):
    """ECS Fargate Task Definition and Cluster for long-running, heavy background jobs.

    Cost Optimization Architecture:
    - NAT Gateways cost ~$32.40/month each (exceeding the entire $50 monthly budget on idle VPC routing).
    - Therefore, this construct provisions a public subnet VPC with 0 NAT Gateways.
    - Fargate tasks run in public subnets with `assignPublicIp: true`, providing direct internet access
      to Bedrock, Telegram, and external tools with zero idle NAT costs.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        app_name: str,
        database: YusufClawDatabase,
        storage: YusufClawStorage,
        bedrock_permissions: BedrockPermissions,
        fargate_cpu: int = 1024,
        fargate_memory_mb: int = 2048,
        container_image: str | None = None,
        existing_vpc: ec2.IVpc | None = None,
    ) -> None:
        super().__init__(scope, id_)

        # 1. VPC with 0 NAT Gateways for budget protection
        self.vpc = existing_vpc or ec2.Vpc(
            self,
            "Vpc",
            vpc_name=f"{app_name}-vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # 2. Security group allowing outbound communication to Bedrock & external APIs
        self.security_group = ec2.SecurityGroup(
            self,
            "FargateSecurityGroup",
            vpc=self.vpc,
            security_group_name=f"{app_name}-heavy-jobs-sg",
            description="Security group for YusufClaw heavy-job Fargate tasks",
            allow_all_outbound=True,
        )

        # 3. ECS Cluster
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=self.vpc,
            cluster_name=f"{app_name}-heavy-jobs-cluster",
            container_insights_v2=ecs.ContainerInsights.DISABLED,  # Save CloudWatch metric fees
        )

        # 4. CloudWatch Log Group with 30-day retention
        self.log_group = logs.LogGroup(
            self,
            "TaskLogGroup",
            log_group_name=f"/ecs/{app_name}-heavy-jobs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # 5. IAM Roles
        execution_role = iam.Role(
            self,
            "TaskExecutionRole",
            role_name=f"{app_name}-fargate-execution-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        task_role = iam.Role(
            self,
            "TaskRole",
            role_name=f"{app_name}-fargate-task-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Application task role for YusufClaw heavy-job containers",
        )

        # Grant Task Role access to Database, Storage, and Bedrock
        database.grant_read_write_data(task_role)
        storage.grant_read_write(task_role)
        bedrock_permissions.grant_invoke(task_role)

        # 6. Fargate Task Definition
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            family=f"{app_name}-heavy-job",
            cpu=fargate_cpu,
            memory_limit_mib=fargate_memory_mb,
            execution_role=execution_role,
            task_role=task_role,
        )

        # 7. Container definition
        self.container = self.task_definition.add_container(
            "HeavyJobRunner",
            container_name=f"{app_name}-heavy-runner",
            image=ecs.ContainerImage.from_registry(
                container_image or "public.ecr.aws/docker/library/python:3.12-slim"
            ),
            logging=ecs.LogDrivers.aws_logs(
                log_group=self.log_group,
                stream_prefix="heavy-job",
            ),
            essential=True,
            environment={
                "APP_NAME": app_name,
                "DYNAMODB_TABLE_NAME": database.table.table_name,
                "FILES_BUCKET_NAME": storage.bucket.bucket_name,
                "BEDROCK_REGION": "us-east-1",
                "PYTHONUNBUFFERED": "1",
            },
        )

    def grant_run_task(self, grantee: iam.IGrantable) -> None:
        """Grant permission to trigger this Fargate Task Definition (e.g. from Telegram Lambda)."""
        run_task_statement = iam.PolicyStatement(
            sid="RunHeavyJobTask",
            effect=iam.Effect.ALLOW,
            actions=["ecs:RunTask"],
            resources=[self.task_definition.task_definition_arn],
            conditions={
                "ArnEquals": {
                    "ecs:cluster": self.cluster.cluster_arn,
                },
            },
        )

        pass_roles: list[str] = []
        if self.task_definition.execution_role:
            pass_roles.append(self.task_definition.execution_role.role_arn)
        pass_roles.append(self.task_definition.task_role.role_arn)

        pass_role_statement = iam.PolicyStatement(
            sid="PassRolesToTask",
            effect=iam.Effect.ALLOW,
            actions=["iam:PassRole"],
            resources=pass_roles,
        )

        grantee.grant_principal.add_to_principal_policy(run_task_statement)
        grantee.grant_principal.add_to_principal_policy(pass_role_statement)
