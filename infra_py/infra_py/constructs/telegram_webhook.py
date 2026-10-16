"""API Gateway + Lambda Telegram Webhook construct for YusufClaw personal AI agent."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra_py.constructs.bedrock_iam import BedrockPermissions
from infra_py.constructs.database import YusufClawDatabase
from infra_py.constructs.heavy_jobs import YusufClawHeavyJobs
from infra_py.constructs.storage import YusufClawStorage


class YusufClawTelegramWebhook(Construct):
    """API Gateway REST API + Lambda Webhook construct for Telegram Bot updates.

    Architecture:
    - Regional REST API Gateway with rate/burst throttling.
    - ARM64 Python 3.12 (or Node.js 20) Lambda function with 512MB RAM and 60s timeout.
    - Integrated permissions for DynamoDB, S3, Bedrock, and ECS RunTask.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        app_name: str,
        database: YusufClawDatabase,
        storage: YusufClawStorage,
        heavy_jobs: YusufClawHeavyJobs,
        bedrock_permissions: BedrockPermissions,
        telegram_bot_token_secret_name: str | None = None,
        telegram_secret_header: str | None = None,
        lambda_runtime: str = "python3.12",
        lambda_code_path: Path | None = None,
    ) -> None:
        super().__init__(scope, id_)

        # 1. CloudWatch Log Group with 30-day retention
        log_group = logs.LogGroup(
            self,
            "TelegramHandlerLogGroup",
            log_group_name=f"/aws/lambda/{app_name}-telegram-webhook",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Resolve lambda asset directory
        code_dir = (
            lambda_code_path
            if lambda_code_path is not None
            else Path(__file__).resolve().parent.parent.parent / "lambda" / "telegram_webhook"
        )

        runtime = (
            lambda_.Runtime.NODEJS_20_X
            if lambda_runtime == "nodejs20.x"
            else lambda_.Runtime.PYTHON_3_12
        )

        # 2. Lambda Function
        self.handler = lambda_.Function(
            self,
            "TelegramHandler",
            function_name=f"{app_name}-telegram-webhook",
            runtime=runtime,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.handler",
            code=lambda_.Code.from_asset(str(code_dir)),
            timeout=cdk.Duration.seconds(60),
            memory_size=512,
            log_group=log_group,
            environment={
                "APP_NAME": app_name,
                "DYNAMODB_TABLE_NAME": database.table.table_name,
                "FILES_BUCKET_NAME": storage.bucket.bucket_name,
                "ECS_CLUSTER_ARN": heavy_jobs.cluster.cluster_arn,
                "ECS_TASK_DEFINITION_ARN": heavy_jobs.task_definition.task_definition_arn,
                "ECS_SUBNET_IDS": ",".join([s.subnet_id for s in heavy_jobs.vpc.public_subnets]),
                "ECS_SECURITY_GROUP_ID": heavy_jobs.security_group.security_group_id,
                "BEDROCK_REGION": "us-east-1",
                "TELEGRAM_BOT_TOKEN_SECRET_NAME": (
                    telegram_bot_token_secret_name or f"{app_name}/telegram/bot-token"
                ),
                "TELEGRAM_SECRET_HEADER": telegram_secret_header or "",
            },
        )

        # 3. Grant Permissions
        database.grant_read_write_data(self.handler)
        storage.grant_read_write(self.handler)
        bedrock_permissions.grant_invoke(self.handler)
        heavy_jobs.grant_run_task(self.handler)

        # 4. API Gateway REST API
        self.api = apigateway.RestApi(
            self,
            "TelegramApi",
            rest_api_name=f"{app_name}-telegram-api",
            description="API Gateway webhook endpoint for YusufClaw Telegram Bot",
            endpoint_types=[apigateway.EndpointType.REGIONAL],
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                throttling_rate_limit=50,
                throttling_burst_limit=100,
                logging_level=apigateway.MethodLoggingLevel.OFF,
            ),
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=["POST", "GET", "OPTIONS"],
            ),
        )

        # 5. Endpoints: POST /webhook/telegram and GET /webhook/telegram
        webhook_resource = self.api.root.add_resource("webhook")
        telegram_resource = webhook_resource.add_resource("telegram")
        lambda_integration = apigateway.LambdaIntegration(
            self.handler,
            proxy=True,
        )

        telegram_resource.add_method("POST", lambda_integration)
        telegram_resource.add_method("GET", lambda_integration)

        self.webhook_url = f"{self.api.url}webhook/telegram"
