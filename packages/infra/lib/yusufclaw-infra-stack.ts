import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { DEFAULT_CONFIG, type YusufClawConfig } from './config';
import { BedrockPermissions } from './constructs/bedrock-iam';
import { YusufClawCostBudget } from './constructs/cost-budget';
import { YusufClawDatabase } from './constructs/database';
import { YusufClawHeavyJobs } from './constructs/heavy-jobs';
import { YusufClawStorage } from './constructs/storage';
import { YusufClawTelegramWebhook } from './constructs/telegram-webhook';

export interface YusufClawInfraStackProps extends cdk.StackProps {
  readonly config?: Partial<YusufClawConfig>;
}

/**
 * YusufClaw Core AWS Infrastructure Stack.
 *
 * Provisions:
 * 1. DynamoDB on-demand memory table (session & turns state)
 * 2. S3 encrypted files bucket with lifecycle management
 * 3. Dedicated Bedrock foundation model and inference profile IAM policies
 * 4. ECS Fargate cluster & task definition in cost-optimized VPC (0 NAT Gateways)
 * 5. API Gateway REST API + ARM64 Node 20 Lambda for Telegram webhooks
 * 6. AWS Budgets monthly 50 USD spending cap with tiered multi-channel notifications
 */
export class YusufClawInfraStack extends cdk.Stack {
  public readonly database: YusufClawDatabase;
  public readonly storage: YusufClawStorage;
  public readonly bedrock: BedrockPermissions;
  public readonly heavyJobs: YusufClawHeavyJobs;
  public readonly telegramWebhook: YusufClawTelegramWebhook;
  public readonly budget: YusufClawCostBudget;

  constructor(scope: Construct, id: string, props?: YusufClawInfraStackProps) {
    super(scope, id, props);

    const config: YusufClawConfig = {
      ...DEFAULT_CONFIG,
      ...props?.config,
    };

    // 1. DynamoDB Memory Table
    this.database = new YusufClawDatabase(this, 'Database', {
      appName: config.appName,
    });

    // 2. S3 Files Storage Bucket
    this.storage = new YusufClawStorage(this, 'Storage', {
      appName: config.appName,
    });

    // 3. AWS Bedrock Invocation IAM Policy
    this.bedrock = new BedrockPermissions(this, 'BedrockPermissions');

    // 4. ECS Fargate Heavy Jobs (0 NAT Gateway cost-optimized architecture)
    this.heavyJobs = new YusufClawHeavyJobs(this, 'HeavyJobs', {
      appName: config.appName,
      database: this.database,
      storage: this.storage,
      bedrockPermissions: this.bedrock,
      fargateCpu: config.fargateCpu,
      fargateMemoryMb: config.fargateMemoryMb,
    });

    // 5. API Gateway -> Lambda Telegram Webhook
    this.telegramWebhook = new YusufClawTelegramWebhook(this, 'TelegramWebhook', {
      appName: config.appName,
      database: this.database,
      storage: this.storage,
      heavyJobs: this.heavyJobs,
      bedrockPermissions: this.bedrock,
      telegramBotTokenSecretName: config.telegramBotTokenSecretName,
    });

    // 6. Monthly 50 USD AWS Budget with alerts
    this.budget = new YusufClawCostBudget(this, 'CostBudget', {
      appName: config.appName,
      amountUsd: config.monthlyBudgetUsd,
      alertEmail: config.alertEmail,
    });

    // ------------------------------------------------------------------------
    // CloudFormation Outputs for easy integration with YusufClaw agent core
    // ------------------------------------------------------------------------
    new cdk.CfnOutput(this, 'TelegramWebhookEndpoint', {
      value: this.telegramWebhook.webhookUrl,
      description: 'Public URL to configure in Telegram setWebhook API',
      exportName: `${config.appName}-telegram-webhook-url`,
    });

    new cdk.CfnOutput(this, 'MemoryTableName', {
      value: this.database.table.tableName,
      description: 'DynamoDB Memory table name',
      exportName: `${config.appName}-dynamodb-table-name`,
    });

    new cdk.CfnOutput(this, 'MemoryTableArn', {
      value: this.database.table.tableArn,
      description: 'DynamoDB Memory table ARN',
    });

    new cdk.CfnOutput(this, 'FilesBucketName', {
      value: this.storage.bucket.bucketName,
      description: 'S3 Files bucket name',
      exportName: `${config.appName}-files-bucket-name`,
    });

    new cdk.CfnOutput(this, 'FilesBucketArn', {
      value: this.storage.bucket.bucketArn,
      description: 'S3 Files bucket ARN',
    });

    new cdk.CfnOutput(this, 'HeavyJobsClusterName', {
      value: this.heavyJobs.cluster.clusterName,
      description: 'ECS Cluster name for heavy jobs',
      exportName: `${config.appName}-ecs-cluster-name`,
    });

    new cdk.CfnOutput(this, 'HeavyJobsTaskDefinitionArn', {
      value: this.heavyJobs.taskDefinition.taskDefinitionArn,
      description: 'ECS Task Definition ARN for heavy jobs',
      exportName: `${config.appName}-fargate-task-def-arn`,
    });

    new cdk.CfnOutput(this, 'BudgetAlertTopicArn', {
      value: this.budget.alertTopic.topicArn,
      description: 'SNS Topic ARN for monthly budget alert notifications',
      exportName: `${config.appName}-budget-alert-topic-arn`,
    });

    new cdk.CfnOutput(this, 'BedrockManagedPolicyArn', {
      value: this.bedrock.managedPolicy.managedPolicyArn,
      description: 'IAM Managed Policy ARN for Bedrock invocations',
    });
  }
}
