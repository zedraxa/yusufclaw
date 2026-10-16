import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { YusufClawInfraStack } from '../lib/yusufclaw-infra-stack';

describe('YusufClawInfraStack', () => {
  let app: cdk.App;
  let stack: YusufClawInfraStack;
  let template: Template;

  beforeAll(() => {
    app = new cdk.App();
    stack = new YusufClawInfraStack(app, 'TestStack', {
      env: { account: '123456789012', region: 'us-east-1' },
      config: {
        alertEmail: 'test-alerts@example.com',
      },
    });
    template = Template.fromStack(stack);
  });

  test('DynamoDB Memory Table is provisioned with On-Demand billing, TTL, and GSI', () => {
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      BillingMode: 'PAY_PER_REQUEST',
      KeySchema: [
        { AttributeName: 'pk', KeyType: 'HASH' },
        { AttributeName: 'sk', KeyType: 'RANGE' },
      ],
      TimeToLiveSpecification: {
        AttributeName: 'ttl',
        Enabled: true,
      },
      GlobalSecondaryIndexes: [
        {
          IndexName: 'GSI1',
          KeySchema: [
            { AttributeName: 'gsi1pk', KeyType: 'HASH' },
            { AttributeName: 'gsi1sk', KeyType: 'RANGE' },
          ],
          Projection: {
            ProjectionType: 'ALL',
          },
        },
      ],
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });
  });

  test('S3 Files Bucket blocks public access, enforces SSL, and has lifecycle rules', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          {
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: 'AES256',
            },
          },
        ],
      },
      VersioningConfiguration: {
        Status: 'Enabled',
      },
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({
            Prefix: 'temp/',
            Status: 'Enabled',
            ExpirationInDays: 14,
          }),
        ]),
      },
    });

    // Enforce SSL Bucket Policy
    template.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 's3:*',
            Effect: 'Deny',
            Condition: {
              Bool: {
                'aws:SecureTransport': 'false',
              },
            },
          }),
        ]),
      },
    });
  });

  test('Bedrock IAM permissions exist for foundation models and inference profiles', () => {
    template.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: Match.arrayWith([
              'bedrock:InvokeModel',
              'bedrock:InvokeModelWithResponseStream',
            ]),
            Effect: 'Allow',
          }),
        ]),
      },
    });
  });

  test('ECS Fargate heavy jobs cluster and task definition with 0 NAT Gateways', () => {
    // 0 NAT Gateways to adhere to $50/mo budget
    template.resourceCountIs('AWS::EC2::NatGateway', 0);

    // ECS Cluster
    template.hasResourceProperties('AWS::ECS::Cluster', {
      ClusterName: 'yusufclaw-heavy-jobs-cluster',
    });

    // Fargate Task Definition (1 vCPU, 2GB RAM)
    template.hasResourceProperties('AWS::ECS::TaskDefinition', {
      Cpu: '1024',
      Memory: '2048',
      RequiresCompatibilities: ['FARGATE'],
      ContainerDefinitions: [
        Match.objectLike({
          Name: 'yusufclaw-heavy-runner',
          Essential: true,
        }),
      ],
    });
  });

  test('Telegram Webhook API Gateway and ARM64 Node 20 Lambda', () => {
    // Lambda Function
    template.hasResourceProperties('AWS::Lambda::Function', {
      Runtime: 'nodejs20.x',
      Architectures: ['arm64'],
      Timeout: 60,
      MemorySize: 512,
    });

    // API Gateway REST API
    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      Name: 'yusufclaw-telegram-api',
      EndpointConfiguration: {
        Types: ['REGIONAL'],
      },
    });

    // POST /webhook/telegram method
    template.hasResourceProperties('AWS::ApiGateway::Method', {
      HttpMethod: 'POST',
      AuthorizationType: 'NONE',
    });
  });

  test('AWS Budget establishes strict 50 USD monthly cap with multi-tier alerts', () => {
    template.hasResourceProperties('AWS::Budgets::Budget', {
      Budget: {
        BudgetLimit: {
          Amount: 50,
          Unit: 'USD',
        },
        BudgetType: 'COST',
        TimeUnit: 'MONTHLY',
      },
      NotificationsWithSubscribers: Match.arrayWith([
        Match.objectLike({
          Notification: {
            ComparisonOperator: 'GREATER_THAN',
            NotificationType: 'ACTUAL',
            Threshold: 50,
            ThresholdType: 'PERCENTAGE',
          },
        }),
        Match.objectLike({
          Notification: {
            ComparisonOperator: 'GREATER_THAN',
            NotificationType: 'ACTUAL',
            Threshold: 80,
            ThresholdType: 'PERCENTAGE',
          },
        }),
        Match.objectLike({
          Notification: {
            ComparisonOperator: 'GREATER_THAN',
            NotificationType: 'ACTUAL',
            Threshold: 100,
            ThresholdType: 'PERCENTAGE',
          },
        }),
        Match.objectLike({
          Notification: {
            ComparisonOperator: 'GREATER_THAN',
            NotificationType: 'FORECASTED',
            Threshold: 100,
            ThresholdType: 'PERCENTAGE',
          },
        }),
      ]),
    });

    // SNS Topic for Budget Alerts
    template.hasResourceProperties('AWS::SNS::Topic', {
      TopicName: 'yusufclaw-budget-alerts',
    });
  });
});
