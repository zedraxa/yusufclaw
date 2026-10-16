import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { BedrockPermissions } from './bedrock-iam';
import { YusufClawDatabase } from './database';
import { YusufClawHeavyJobs } from './heavy-jobs';
import { YusufClawStorage } from './storage';

export interface TelegramWebhookProps {
  readonly appName: string;
  readonly database: YusufClawDatabase;
  readonly storage: YusufClawStorage;
  readonly heavyJobs: YusufClawHeavyJobs;
  readonly bedrockPermissions: BedrockPermissions;
  readonly telegramBotTokenSecretName?: string;
  readonly telegramSecretHeader?: string;
}

/**
 * API Gateway REST API + Lambda Webhook construct for Telegram Bot updates.
 *
 * Architecture:
 * - Regional REST API Gateway with rate/burst throttling.
 * - ARM64 Node.js 20 Lambda function with 512MB RAM and 60s timeout.
 * - Integrated permissions for DynamoDB, S3, Bedrock, and ECS RunTask.
 */
export class YusufClawTelegramWebhook extends Construct {
  public readonly handler: lambda.Function;
  public readonly api: apigateway.RestApi;
  public readonly webhookUrl: string;

  constructor(scope: Construct, id: string, props: TelegramWebhookProps) {
    super(scope, id);

    // 1. CloudWatch Log Group with 30-day retention
    const logGroup = new logs.LogGroup(this, 'TelegramHandlerLogGroup', {
      logGroupName: `/aws/lambda/${props.appName}-telegram-webhook`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // 2. Lambda Function
    this.handler = new lambda.Function(this, 'TelegramHandler', {
      functionName: `${props.appName}-telegram-webhook`,
      runtime: lambda.Runtime.NODEJS_20_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/telegram-webhook')),
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      logGroup,
      environment: {
        APP_NAME: props.appName,
        DYNAMODB_TABLE_NAME: props.database.table.tableName,
        FILES_BUCKET_NAME: props.storage.bucket.bucketName,
        ECS_CLUSTER_ARN: props.heavyJobs.cluster.clusterArn,
        ECS_TASK_DEFINITION_ARN: props.heavyJobs.taskDefinition.taskDefinitionArn,
        ECS_SUBNET_IDS: props.heavyJobs.vpc.publicSubnets.map(s => s.subnetId).join(','),
        ECS_SECURITY_GROUP_ID: props.heavyJobs.securityGroup.securityGroupId,
        BEDROCK_REGION: 'us-east-1',
        TELEGRAM_BOT_TOKEN_SECRET_NAME: props.telegramBotTokenSecretName ?? `${props.appName}/telegram/bot-token`,
        TELEGRAM_SECRET_HEADER: props.telegramSecretHeader ?? '',
      },
    });

    // 2. Grant Permissions
    props.database.grantReadWriteData(this.handler);
    props.storage.grantReadWrite(this.handler);
    props.bedrockPermissions.grantInvoke(this.handler);
    props.heavyJobs.grantRunTask(this.handler);

    // 3. API Gateway REST API
    this.api = new apigateway.RestApi(this, 'TelegramApi', {
      restApiName: `${props.appName}-telegram-api`,
      description: 'API Gateway webhook endpoint for YusufClaw Telegram Bot',
      endpointTypes: [apigateway.EndpointType.REGIONAL],
      deployOptions: {
        stageName: 'prod',
        throttlingRateLimit: 50,
        throttlingBurstLimit: 100,
        loggingLevel: apigateway.MethodLoggingLevel.OFF,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: ['POST', 'GET', 'OPTIONS'],
      },
    });

    // 4. Endpoints: POST /webhook/telegram and GET /webhook/telegram
    const webhookResource = this.api.root.addResource('webhook');
    const telegramResource = webhookResource.addResource('telegram');
    const lambdaIntegration = new apigateway.LambdaIntegration(this.handler, {
      proxy: true,
    });

    telegramResource.addMethod('POST', lambdaIntegration);
    telegramResource.addMethod('GET', lambdaIntegration);

    this.webhookUrl = `${this.api.url}webhook/telegram`;
  }
}
