"""Unit tests for YusufClaw Python CDK infrastructure stack and constructs."""

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra_py.config import YusufClawConfig
from infra_py.stack import YusufClawInfraStack

_lambda_path = Path(__file__).resolve().parent.parent / "lambda" / "telegram_webhook" / "index.py"
_spec = importlib.util.spec_from_file_location("telegram_webhook_index", _lambda_path)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
lambda_handler = _mod.handler


@pytest.fixture(scope="module")
def stack_template() -> Template:
    """Synthesize the default YusufClaw infrastructure stack for assertions."""
    app = cdk.App()
    stack = YusufClawInfraStack(
        app,
        "TestStack",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
        config=YusufClawConfig(
            alert_email="test-alerts@example.com",
        ),
    )
    return Template.from_stack(stack)


def test_dynamodb_memory_table(stack_template: Template) -> None:
    """DynamoDB Memory Table is provisioned with On-Demand billing, TTL, and GSI."""
    stack_template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "TimeToLiveSpecification": {
                "AttributeName": "ttl",
                "Enabled": True,
            },
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {
                        "ProjectionType": "ALL",
                    },
                },
            ],
            "PointInTimeRecoverySpecification": {
                "PointInTimeRecoveryEnabled": True,
            },
        },
    )


def test_s3_files_bucket(stack_template: Template) -> None:
    """S3 Files Bucket blocks public access, enforces SSL, and has lifecycle rules."""
    stack_template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {
                        "ServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256",
                        },
                    },
                ],
            },
            "VersioningConfiguration": {
                "Status": "Enabled",
            },
            "LifecycleConfiguration": {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Prefix": "temp/",
                                "Status": "Enabled",
                                "ExpirationInDays": 14,
                            }
                        ),
                    ]
                ),
            },
        },
    )

    # Enforce SSL Bucket Policy
    stack_template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": "s3:*",
                                "Effect": "Deny",
                                "Condition": {
                                    "Bool": {
                                        "aws:SecureTransport": "false",
                                    },
                                },
                            }
                        ),
                    ]
                ),
            },
        },
    )


def test_bedrock_iam_permissions(stack_template: Template) -> None:
    """Bedrock IAM permissions exist for foundation models and inference profiles."""
    stack_template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": Match.array_with(
                                    [
                                        "bedrock:InvokeModel",
                                        "bedrock:InvokeModelWithResponseStream",
                                    ]
                                ),
                                "Effect": "Allow",
                            }
                        ),
                    ]
                ),
            },
        },
    )


def test_fargate_heavy_jobs_0_nat(stack_template: Template) -> None:
    """ECS Fargate heavy jobs cluster and task definition provisioned with 0 NAT Gateways."""
    # Strict 0 NAT Gateways to adhere to $50/mo budget
    stack_template.resource_count_is("AWS::EC2::NatGateway", 0)

    # ECS Cluster
    stack_template.has_resource_properties(
        "AWS::ECS::Cluster",
        {
            "ClusterName": "yusufclaw-heavy-jobs-cluster",
        },
    )

    # Fargate Task Definition (1 vCPU, 2GB RAM)
    stack_template.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Cpu": "1024",
            "Memory": "2048",
            "RequiresCompatibilities": ["FARGATE"],
            "ContainerDefinitions": [
                Match.object_like(
                    {
                        "Name": "yusufclaw-heavy-runner",
                        "Essential": True,
                    }
                ),
            ],
        },
    )


def test_telegram_webhook_api_gateway_and_lambda(stack_template: Template) -> None:
    """Telegram Webhook API Gateway and ARM64 Lambda are provisioned."""
    # Lambda Function
    stack_template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Architectures": ["arm64"],
            "Timeout": 60,
            "MemorySize": 512,
        },
    )

    # API Gateway REST API
    stack_template.has_resource_properties(
        "AWS::ApiGateway::RestApi",
        {
            "Name": "yusufclaw-telegram-api",
            "EndpointConfiguration": {
                "Types": ["REGIONAL"],
            },
        },
    )

    # POST /webhook/telegram method
    stack_template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "HttpMethod": "POST",
            "AuthorizationType": "NONE",
        },
    )

    # GET /webhook/telegram method
    stack_template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "HttpMethod": "GET",
            "AuthorizationType": "NONE",
        },
    )


def test_monthly_budget_50_usd(stack_template: Template) -> None:
    """AWS Budget establishes strict 50 USD monthly cap with multi-tier alerts."""
    stack_template.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": {
                "BudgetLimit": {
                    "Amount": 50,
                    "Unit": "USD",
                },
                "BudgetType": "COST",
                "TimeUnit": "MONTHLY",
            },
            "NotificationsWithSubscribers": Match.array_with(
                [
                    Match.object_like(
                        {
                            "Notification": {
                                "ComparisonOperator": "GREATER_THAN",
                                "NotificationType": "ACTUAL",
                                "Threshold": 50,
                                "ThresholdType": "PERCENTAGE",
                            },
                        }
                    ),
                    Match.object_like(
                        {
                            "Notification": {
                                "ComparisonOperator": "GREATER_THAN",
                                "NotificationType": "ACTUAL",
                                "Threshold": 80,
                                "ThresholdType": "PERCENTAGE",
                            },
                        }
                    ),
                    Match.object_like(
                        {
                            "Notification": {
                                "ComparisonOperator": "GREATER_THAN",
                                "NotificationType": "ACTUAL",
                                "Threshold": 100,
                                "ThresholdType": "PERCENTAGE",
                            },
                        }
                    ),
                    Match.object_like(
                        {
                            "Notification": {
                                "ComparisonOperator": "GREATER_THAN",
                                "NotificationType": "FORECASTED",
                                "Threshold": 100,
                                "ThresholdType": "PERCENTAGE",
                            },
                        }
                    ),
                ]
            ),
        },
    )

    # SNS Topic for Budget Alerts
    stack_template.has_resource_properties(
        "AWS::SNS::Topic",
        {
            "TopicName": "yusufclaw-budget-alerts",
        },
    )


def test_cloudformation_outputs(stack_template: Template) -> None:
    """Verify all required CloudFormation outputs are present."""
    template_dict = stack_template.to_json()
    outputs = template_dict.get("Outputs", {})

    export_names = {
        out.get("Export", {}).get("Name") for out in outputs.values() if "Export" in out
    }

    assert "yusufclaw-telegram-webhook-url" in export_names
    assert "yusufclaw-dynamodb-table-name" in export_names
    assert "yusufclaw-files-bucket-name" in export_names
    assert "yusufclaw-ecs-cluster-name" in export_names
    assert "yusufclaw-fargate-task-def-arn" in export_names
    assert "yusufclaw-budget-alert-topic-arn" in export_names


def test_custom_config_override() -> None:
    """Custom configuration propagates correctly to CloudFormation template."""
    app = cdk.App()
    custom_stack = YusufClawInfraStack(
        app,
        "CustomStack",
        env=cdk.Environment(account="999888777666", region="us-east-1"),
        config=YusufClawConfig(
            app_name="customclaw",
            monthly_budget_usd=75.0,
            fargate_cpu=2048,
            fargate_memory_mb=4096,
            alert_email="ops@customclaw.io",
        ),
    )
    tmpl = Template.from_stack(custom_stack)

    # Custom table name
    tmpl.has_resource_properties("AWS::DynamoDB::Table", {"TableName": "customclaw-memory"})

    # Custom budget limit
    tmpl.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": {
                "BudgetLimit": {"Amount": 75, "Unit": "USD"},
                "BudgetName": "customclaw-monthly-75-usd-budget",
            }
        },
    )

    # Custom Fargate CPU & RAM
    tmpl.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Cpu": "2048",
            "Memory": "4096",
        },
    )


# -----------------------------------------------------------------------------
# Lambda Handler Unit Tests
# -----------------------------------------------------------------------------


def test_lambda_handler_get_health_check() -> None:
    """GET /webhook/telegram returns 200 health check response."""
    event: dict[str, Any] = {"httpMethod": "GET"}
    res = lambda_handler(event)
    assert res["statusCode"] == 200
    body = json.loads(res["body"])
    assert body["status"] == "healthy"
    assert body["service"] == "yusufclaw-telegram-webhook"


def test_lambda_handler_invalid_method() -> None:
    """Non-POST/GET method returns 405 Method Not Allowed."""
    event: dict[str, Any] = {"httpMethod": "PUT"}
    res = lambda_handler(event)
    assert res["statusCode"] == 405


def test_lambda_handler_empty_body() -> None:
    """POST with empty body returns 400 Bad Request."""
    event: dict[str, Any] = {"httpMethod": "POST", "body": ""}
    res = lambda_handler(event)
    assert res["statusCode"] == 400


def test_lambda_handler_secret_header_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret header token is validated when configured."""
    monkeypatch.setenv("TELEGRAM_SECRET_HEADER", "my-secret-token")

    # Mismatch
    event_bad: dict[str, Any] = {
        "httpMethod": "POST",
        "headers": {"x-telegram-bot-api-secret-token": "wrong"},
        "body": '{"update_id": 1}',
    }
    assert lambda_handler(event_bad)["statusCode"] == 403

    # Match
    event_ok: dict[str, Any] = {
        "httpMethod": "POST",
        "headers": {"x-telegram-bot-api-secret-token": "my-secret-token"},
        "body": '{"update_id": 12345, "message": {"text": "hello", "chat": {"id": 1}}}',
    }
    res = lambda_handler(event_ok)
    assert res["statusCode"] == 200
    assert json.loads(res["body"])["ok"] is True


def test_lambda_handler_base64_encoded() -> None:
    """Base64 encoded payload is decoded and processed."""
    payload = json.dumps({"update_id": 9999, "message": {"text": "test", "chat": {"id": 42}}})
    encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")

    event: dict[str, Any] = {
        "httpMethod": "POST",
        "isBase64Encoded": True,
        "body": encoded,
    }
    res = lambda_handler(event)
    assert res["statusCode"] == 200
    body = json.loads(res["body"])
    assert body["ok"] is True
    assert body["update_id"] == 9999


def test_lambda_handler_malformed_json() -> None:
    """Malformed JSON returns 200 ok=False to avoid Telegram infinite retry loops."""
    event: dict[str, Any] = {
        "httpMethod": "POST",
        "body": "not-valid-json",
    }
    res = lambda_handler(event)
    assert res["statusCode"] == 200
    body = json.loads(res["body"])
    assert body["ok"] is False
    assert body["error"] == "Malformed payload"
