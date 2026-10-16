#!/usr/bin/env python3
"""YusufClaw AWS CDK Python Application Entrypoint."""

import os

import aws_cdk as cdk

from infra_py.config import DEFAULT_CONFIG
from infra_py.stack import YusufClawInfraStack

app = cdk.App()

# Target region: us-east-1 as specified in SPEC.md
region = (
    app.node.try_get_context("region")
    or os.environ.get("CDK_DEFAULT_REGION")
    or DEFAULT_CONFIG.region
)
account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")

stack = YusufClawInfraStack(
    app,
    "YusufClawInfraStack",
    env=cdk.Environment(
        account=account,
        region=region,
    ),
    description="YusufClaw personal AI agent AWS infrastructure (us-east-1)",
)

# Resource tagging for cost allocation and inventory tracking
cdk.Tags.of(stack).add("Project", "yusufclaw")
cdk.Tags.of(stack).add("ManagedBy", "AWS-CDK")
cdk.Tags.of(stack).add("Owner", "yusuf")
cdk.Tags.of(stack).add("Budget", "50USD")

app.synth()
