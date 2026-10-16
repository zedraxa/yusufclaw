import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { BedrockPermissions } from './bedrock-iam';
import { YusufClawDatabase } from './database';
import { YusufClawStorage } from './storage';

export interface HeavyJobsProps {
  readonly appName: string;
  readonly database: YusufClawDatabase;
  readonly storage: YusufClawStorage;
  readonly bedrockPermissions: BedrockPermissions;
  readonly fargateCpu?: number;
  readonly fargateMemoryMb?: number;
  readonly containerImage?: string;
  readonly existingVpc?: ec2.IVpc;
}

/**
 * ECS Fargate Task Definition and Cluster for long-running, heavy background jobs.
 *
 * Cost Optimization Architecture:
 * - NAT Gateways cost ~$32.40/month each (exceeding the entire $50 monthly budget on idle VPC routing).
 * - Therefore, this construct provisions a public subnet VPC with 0 NAT Gateways.
 * - Fargate tasks run in public subnets with `assignPublicIp: true`, providing direct internet access
 *   to Bedrock, Telegram, and external tools with zero idle NAT costs.
 */
export class YusufClawHeavyJobs extends Construct {
  public readonly vpc: ec2.IVpc;
  public readonly cluster: ecs.Cluster;
  public readonly taskDefinition: ecs.FargateTaskDefinition;
  public readonly securityGroup: ec2.SecurityGroup;
  public readonly logGroup: logs.LogGroup;
  public readonly container: ecs.ContainerDefinition;

  constructor(scope: Construct, id: string, props: HeavyJobsProps) {
    super(scope, id);

    // 1. VPC with 0 NAT Gateways for budget protection
    this.vpc = props.existingVpc ?? new ec2.Vpc(this, 'Vpc', {
      vpcName: `${props.appName}-vpc`,
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    // 2. Security group allowing outbound communication to Bedrock & external APIs
    this.securityGroup = new ec2.SecurityGroup(this, 'FargateSecurityGroup', {
      vpc: this.vpc,
      securityGroupName: `${props.appName}-heavy-jobs-sg`,
      description: 'Security group for YusufClaw heavy-job Fargate tasks',
      allowAllOutbound: true,
    });

    // 3. ECS Cluster
    this.cluster = new ecs.Cluster(this, 'Cluster', {
      vpc: this.vpc,
      clusterName: `${props.appName}-heavy-jobs-cluster`,
      containerInsightsV2: ecs.ContainerInsights.DISABLED, // Save CloudWatch metric fees
    });

    // 4. CloudWatch Log Group with 30-day retention
    this.logGroup = new logs.LogGroup(this, 'TaskLogGroup', {
      logGroupName: `/ecs/${props.appName}-heavy-jobs`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // 5. IAM Roles
    const executionRole = new iam.Role(this, 'TaskExecutionRole', {
      roleName: `${props.appName}-fargate-execution-role`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });

    const taskRole = new iam.Role(this, 'TaskRole', {
      roleName: `${props.appName}-fargate-task-role`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      description: 'Application task role for YusufClaw heavy-job containers',
    });

    // Grant Task Role access to Database, Storage, and Bedrock
    props.database.grantReadWriteData(taskRole);
    props.storage.grantReadWrite(taskRole);
    props.bedrockPermissions.grantInvoke(taskRole);

    // 6. Fargate Task Definition
    this.taskDefinition = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      family: `${props.appName}-heavy-job`,
      cpu: props.fargateCpu ?? 1024,
      memoryLimitMiB: props.fargateMemoryMb ?? 2048,
      executionRole,
      taskRole,
    });

    // 7. Container definition
    this.container = this.taskDefinition.addContainer('HeavyJobRunner', {
      containerName: `${props.appName}-heavy-runner`,
      image: ecs.ContainerImage.fromRegistry(
        props.containerImage ?? 'public.ecr.aws/docker/library/node:20-slim'
      ),
      logging: ecs.LogDrivers.awsLogs({
        logGroup: this.logGroup,
        streamPrefix: 'heavy-job',
      }),
      essential: true,
      environment: {
        APP_NAME: props.appName,
        DYNAMODB_TABLE_NAME: props.database.table.tableName,
        FILES_BUCKET_NAME: props.storage.bucket.bucketName,
        BEDROCK_REGION: 'us-east-1',
        NODE_ENV: 'production',
      },
    });
  }

  /**
   * Grant permission to trigger this Fargate Task Definition (e.g. from Telegram Lambda)
   */
  public grantRunTask(grantee: iam.IGrantable): void {
    const runTaskStatement = new iam.PolicyStatement({
      sid: 'RunHeavyJobTask',
      effect: iam.Effect.ALLOW,
      actions: ['ecs:RunTask'],
      resources: [this.taskDefinition.taskDefinitionArn],
      conditions: {
        ArnEquals: {
          'ecs:cluster': this.cluster.clusterArn,
        },
      },
    });

    const passRoleStatement = new iam.PolicyStatement({
      sid: 'PassRolesToTask',
      effect: iam.Effect.ALLOW,
      actions: ['iam:PassRole'],
      resources: [
        this.taskDefinition.executionRole!.roleArn,
        this.taskDefinition.taskRole.roleArn,
      ],
    });

    grantee.grantPrincipal.addToPrincipalPolicy(runTaskStatement);
    grantee.grantPrincipal.addToPrincipalPolicy(passRoleStatement);
  }
}
