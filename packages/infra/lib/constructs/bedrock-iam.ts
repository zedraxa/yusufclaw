import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface BedrockPermissionsProps {
  /** Optional specific model IDs to constrain access. Defaults to wildcard foundation models and inference profiles */
  readonly allowedModelIds?: string[];
}

/**
 * Bedrock IAM Policy Construct.
 * Configures least-privilege IAM permissions for invoking Bedrock Foundation Models,
 * Converse API, streaming, and cross-region inference profiles.
 */
export class BedrockPermissions extends Construct {
  public readonly policyStatement: iam.PolicyStatement;
  public readonly managedPolicy: iam.ManagedPolicy;

  constructor(scope: Construct, id: string, props?: BedrockPermissionsProps) {
    super(scope, id);

    const foundationModelResources = props?.allowedModelIds?.length
      ? props.allowedModelIds.map(id => `arn:aws:bedrock:*::foundation-model/${id}`)
      : ['arn:aws:bedrock:*::foundation-model/*'];

    this.policyStatement = new iam.PolicyStatement({
      sid: 'BedrockModelInvocation',
      effect: iam.Effect.ALLOW,
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
        'bedrock:GetFoundationModel',
        'bedrock:ListFoundationModels',
        'bedrock:GetInferenceProfile',
        'bedrock:ListInferenceProfiles',
        'bedrock:ApplyGuardrail',
      ],
      resources: [
        ...foundationModelResources,
        `arn:${cdk.Aws.PARTITION}:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
        `arn:${cdk.Aws.PARTITION}:bedrock:*:${cdk.Aws.ACCOUNT_ID}:application-inference-profile/*`,
        `arn:${cdk.Aws.PARTITION}:bedrock:*:${cdk.Aws.ACCOUNT_ID}:guardrail/*`,
      ],
    });

    this.managedPolicy = new iam.ManagedPolicy(this, 'BedrockInvokeManagedPolicy', {
      description: 'Allows invoking AWS Bedrock foundation models and inference profiles for YusufClaw',
      statements: [this.policyStatement],
    });
  }

  /**
   * Grant Bedrock invocation permissions to an IAM grantee (Lambda role, ECS Task role, etc.)
   */
  public grantInvoke(grantee: iam.IGrantable): void {
    grantee.grantPrincipal.addToPrincipalPolicy(this.policyStatement);
  }
}
