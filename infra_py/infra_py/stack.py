"""YusufClaw Core AWS Infrastructure Stack (Python CDK)."""

from typing import Any

import aws_cdk as cdk
from constructs import Construct

from infra_py.config import DEFAULT_CONFIG, YusufClawConfig
from infra_py.constructs.bedrock_iam import BedrockPermissions
from infra_py.constructs.cost_budget import YusufClawCostBudget
from infra_py.constructs.database import YusufClawDatabase
from infra_py.constructs.heavy_jobs import YusufClawHeavyJobs
from infra_py.constructs.storage import YusufClawStorage
from infra_py.constructs.telegram_webhook import YusufClawTelegramWebhook


class YusufClawInfraStack(cdk.Stack):
    """YusufClaw Core AWS Infrastructure Stack.

    Provisions:
    1. DynamoDB on-demand memory table (session & turns state)
    2. S3 encrypted files bucket with lifecycle management
    3. Dedicated Bedrock foundation model and inference profile IAM policies
    4. ECS Fargate cluster & task definition in cost-optimized VPC (0 NAT Gateways)
    5. API Gateway REST API + ARM64 Python Lambda for Telegram webhooks
    6. AWS Budgets monthly 50 USD spending cap with tiered multi-channel notifications
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        config: YusufClawConfig | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id_, **kwargs)

        cfg = config or DEFAULT_CONFIG

        # 1. DynamoDB Memory Table
        self.database = YusufClawDatabase(
            self,
            "Database",
            app_name=cfg.app_name,
        )

        # 2. S3 Files Storage Bucket
        self.storage = YusufClawStorage(
            self,
            "Storage",
            app_name=cfg.app_name,
        )

        # 3. AWS Bedrock Invocation IAM Policy
        self.bedrock = BedrockPermissions(
            self,
            "BedrockPermissions",
        )

        # 4. ECS Fargate Heavy Jobs (0 NAT Gateway cost-optimized architecture)
        self.heavy_jobs = YusufClawHeavyJobs(
            self,
            "HeavyJobs",
            app_name=cfg.app_name,
            database=self.database,
            storage=self.storage,
            bedrock_permissions=self.bedrock,
            fargate_cpu=cfg.fargate_cpu,
            fargate_memory_mb=cfg.fargate_memory_mb,
            container_image=cfg.fargate_container_image,
        )

        # 5. API Gateway -> Lambda Telegram Webhook
        self.telegram_webhook = YusufClawTelegramWebhook(
            self,
            "TelegramWebhook",
            app_name=cfg.app_name,
            database=self.database,
            storage=self.storage,
            heavy_jobs=self.heavy_jobs,
            bedrock_permissions=self.bedrock,
            telegram_bot_token_secret_name=cfg.telegram_bot_token_secret_name,
            telegram_secret_header=cfg.telegram_secret_header,
            lambda_runtime=cfg.lambda_runtime,
        )

        # 6. Monthly 50 USD AWS Budget with alerts
        self.budget = YusufClawCostBudget(
            self,
            "CostBudget",
            app_name=cfg.app_name,
            amount_usd=cfg.monthly_budget_usd,
            alert_email=cfg.alert_email,
        )

        # ------------------------------------------------------------------------
        # CloudFormation Outputs for easy integration with YusufClaw agent core
        # ------------------------------------------------------------------------
        cdk.CfnOutput(
            self,
            "TelegramWebhookEndpoint",
            value=self.telegram_webhook.webhook_url,
            description="Public URL to configure in Telegram setWebhook API",
            export_name=f"{cfg.app_name}-telegram-webhook-url",
        )

        cdk.CfnOutput(
            self,
            "MemoryTableName",
            value=self.database.table.table_name,
            description="DynamoDB Memory table name",
            export_name=f"{cfg.app_name}-dynamodb-table-name",
        )

        cdk.CfnOutput(
            self,
            "MemoryTableArn",
            value=self.database.table.table_arn,
            description="DynamoDB Memory table ARN",
        )

        cdk.CfnOutput(
            self,
            "FilesBucketName",
            value=self.storage.bucket.bucket_name,
            description="S3 Files bucket name",
            export_name=f"{cfg.app_name}-files-bucket-name",
        )

        cdk.CfnOutput(
            self,
            "FilesBucketArn",
            value=self.storage.bucket.bucket_arn,
            description="S3 Files bucket ARN",
        )

        cdk.CfnOutput(
            self,
            "HeavyJobsClusterName",
            value=self.heavy_jobs.cluster.cluster_name,
            description="ECS Cluster name for heavy jobs",
            export_name=f"{cfg.app_name}-ecs-cluster-name",
        )

        cdk.CfnOutput(
            self,
            "HeavyJobsTaskDefinitionArn",
            value=self.heavy_jobs.task_definition.task_definition_arn,
            description="ECS Task Definition ARN for heavy jobs",
            export_name=f"{cfg.app_name}-fargate-task-def-arn",
        )

        cdk.CfnOutput(
            self,
            "BudgetAlertTopicArn",
            value=self.budget.alert_topic.topic_arn,
            description="SNS Topic ARN for monthly budget alert notifications",
            export_name=f"{cfg.app_name}-budget-alert-topic-arn",
        )

        cdk.CfnOutput(
            self,
            "BedrockManagedPolicyArn",
            value=self.bedrock.managed_policy.managed_policy_arn,
            description="IAM Managed Policy ARN for Bedrock invocations",
        )
