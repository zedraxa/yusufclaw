"""Bedrock IAM Permissions construct for YusufClaw personal AI agent."""

from collections.abc import Sequence

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct


class BedrockPermissions(Construct):
    """Bedrock IAM Policy Construct.

    Configures least-privilege IAM permissions for invoking Bedrock Foundation Models,
    Converse API, streaming, and cross-region inference profiles.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        allowed_model_ids: Sequence[str] | None = None,
    ) -> None:
        super().__init__(scope, id_)

        foundation_model_resources = (
            [f"arn:aws:bedrock:*::foundation-model/{model_id}" for model_id in allowed_model_ids]
            if allowed_model_ids
            else ["arn:aws:bedrock:*::foundation-model/*"]
        )

        self.policy_statement = iam.PolicyStatement(
            sid="BedrockModelInvocation",
            effect=iam.Effect.ALLOW,
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:GetFoundationModel",
                "bedrock:ListFoundationModels",
                "bedrock:GetInferenceProfile",
                "bedrock:ListInferenceProfiles",
                "bedrock:ApplyGuardrail",
            ],
            resources=[
                *foundation_model_resources,
                f"arn:{cdk.Aws.PARTITION}:bedrock:*:{cdk.Aws.ACCOUNT_ID}:inference-profile/*",
                f"arn:{cdk.Aws.PARTITION}:bedrock:*:{cdk.Aws.ACCOUNT_ID}:application-inference-profile/*",
                f"arn:{cdk.Aws.PARTITION}:bedrock:*:{cdk.Aws.ACCOUNT_ID}:guardrail/*",
            ],
        )

        self.managed_policy = iam.ManagedPolicy(
            self,
            "BedrockInvokeManagedPolicy",
            description="Allows invoking AWS Bedrock foundation models and inference profiles for YusufClaw",
            statements=[self.policy_statement],
        )

    def grant_invoke(self, grantee: iam.IGrantable) -> None:
        """Grant Bedrock invocation permissions to an IAM grantee."""
        grantee.grant_principal.add_to_principal_policy(self.policy_statement)
