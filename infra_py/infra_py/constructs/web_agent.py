"""Internet-facing YusufClaw web UI + AG-UI gateway (Lambda container + Function URL)."""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from infra_py.constructs.bedrock_iam import BedrockPermissions
from infra_py.constructs.database import YusufClawDatabase
from infra_py.constructs.storage import YusufClawStorage

REPO_ROOT = Path(__file__).resolve().parents[3]

ASSET_EXCLUDES = [
    ".git",
    "**/.venv",
    "**/node_modules",
    "**/__pycache__",
    "**/.mypy_cache",
    "**/.ruff_cache",
    "**/.pytest_cache",
    "**/cdk.out",
    ".data",
    ".env",
    ".env.*",
    "docs",
    "evals",
    "tests",
    "infra_py/tests",
]


class YusufClawWebAgent(Construct):
    """Web chat UI + AG-UI SSE backend reachable over HTTPS.

    Cost profile: scale-to-zero Lambda (no ALB, no NAT, no always-on compute).
    Authentication: the gateway requires `Authorization: Bearer <token>` on every API call.
    The token is generated at deploy time and stored only in Secrets Manager.
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
        bedrock_model_id: str,
        auth_secret_name: str,
        memory_mb: int = 1024,
        code: lambda_.DockerImageCode | None = None,
    ) -> None:
        super().__init__(scope, id_)

        self.auth_secret = secretsmanager.Secret(
            self,
            "WebAuthToken",
            secret_name=auth_secret_name,
            description="Bearer token for the YusufClaw web UI (paste into the Access token field)",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=48, exclude_punctuation=True
            ),
        )

        log_group = logs.LogGroup(
            self,
            "WebLogGroup",
            log_group_name=f"/aws/lambda/{app_name}-web",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        image_code = code or lambda_.DockerImageCode.from_image_asset(
            str(REPO_ROOT),
            file="infra_py/web_lambda/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
            exclude=ASSET_EXCLUDES,
        )

        self.handler = lambda_.DockerImageFunction(
            self,
            "WebHandler",
            function_name=f"{app_name}-web",
            code=image_code,
            architecture=lambda_.Architecture.X86_64,
            memory_size=memory_mb,
            timeout=cdk.Duration.minutes(15),
            log_group=log_group,
            environment={
                "WEB_AUTH_SECRET_ARN": self.auth_secret.secret_arn,
                "MODEL_PROVIDER": "bedrock",
                "BEDROCK_MODEL_ID": bedrock_model_id,
                "DYNAMODB_TABLE_NAME": database.table.table_name,
                "FILES_BUCKET_NAME": storage.bucket.bucket_name,
            },
        )
        self.auth_secret.grant_read(self.handler)
        bedrock_permissions.grant_invoke(self.handler)
        database.grant_read_write_data(self.handler)
        storage.grant_read_write(self.handler)

        self.function_url = self.handler.add_function_url(
            # Browsers cannot SigV4-sign; auth is enforced by the app's bearer token.
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
        )
