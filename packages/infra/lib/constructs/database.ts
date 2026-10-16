import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface DatabaseProps {
  readonly appName: string;
  readonly removalPolicy?: cdk.RemovalPolicy;
}

/**
 * DynamoDB Single-Table Memory storage for YusufClaw agent.
 * Designed for serverless pay-per-request billing (0 idle cost) to respect the $50 monthly budget.
 *
 * Schema:
 * - PK: Partition Key (e.g. `SESSION#<sessionId>`, `STATE#<key>`)
 * - SK: Sort Key (e.g. `TURN#<timestamp>#<requestId>`, `METADATA`)
 * - GSI1PK / GSI1SK: Secondary index for querying by request ID or turn types.
 * - TTL: Unix epoch timestamp for automatic ephemeral session expiration.
 */
export class YusufClawDatabase extends Construct {
  public readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DatabaseProps) {
    super(scope, id);

    this.table = new dynamodb.Table(this, 'MemoryTable', {
      tableName: `${props.appName}-memory`,
      partitionKey: {
        name: 'pk',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'sk',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: true,
      },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy: props.removalPolicy ?? cdk.RemovalPolicy.RETAIN,
    });

    // Add GSI for inverted lookups / request filtering
    this.table.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: {
        name: 'gsi1pk',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'gsi1sk',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });
  }

  public grantReadData(grantee: iam.IGrantable): iam.Grant {
    return this.table.grantReadData(grantee);
  }

  public grantWriteData(grantee: iam.IGrantable): iam.Grant {
    return this.table.grantWriteData(grantee);
  }

  public grantReadWriteData(grantee: iam.IGrantable): iam.Grant {
    return this.table.grantReadWriteData(grantee);
  }
}
