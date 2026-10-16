"""Configuration defaults and constants for YusufClaw AWS Infrastructure."""

from dataclasses import dataclass


@dataclass(frozen=True)
class YusufClawConfig:
    """Strongly-typed configuration for YusufClaw CDK infrastructure."""

    # AWS Profile to use
    profile: str = "yusufclaw"
    # Primary AWS deployment region
    region: str = "us-east-1"
    # Application / Project prefix
    app_name: str = "yusufclaw"
    # Monthly AWS spending limit in USD
    monthly_budget_usd: float = 50.0
    # Notification email for budget and operational alerts
    alert_email: str | None = "alerts@yusufclaw.local"
    # Fargate heavy-job task CPU units (1024 = 1 vCPU)
    fargate_cpu: int = 1024
    # Fargate heavy-job task memory in MB
    fargate_memory_mb: int = 2048
    # Telegram bot token secret name in AWS Secrets Manager / Parameter Store
    telegram_bot_token_secret_name: str = "yusufclaw/telegram/bot-token"
    # Default Bedrock model ID
    default_bedrock_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    # Optional Telegram webhook secret token header for verification
    telegram_secret_header: str | None = None
    # Container image for Fargate heavy jobs (default: Python 3.12 slim)
    fargate_container_image: str = "public.ecr.aws/docker/library/python:3.12-slim"
    # Lambda runtime for Telegram webhook handler
    lambda_runtime: str = "python3.12"


DEFAULT_CONFIG = YusufClawConfig()
