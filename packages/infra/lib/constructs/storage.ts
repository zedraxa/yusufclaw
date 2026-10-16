import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';

export interface StorageProps {
  readonly appName: string;
  readonly removalPolicy?: cdk.RemovalPolicy;
}

/**
 * S3 Storage for YusufClaw agent files, attachments, tool scratch spaces, and media.
 * Configured with SSL enforcement, strict public access blocking, versioning,
 * and cost-effective lifecycle expiration for temporary files.
 */
export class YusufClawStorage extends Construct {
  public readonly bucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: StorageProps) {
    super(scope, id);

    this.bucket = new s3.Bucket(this, 'FilesBucket', {
      bucketName: `${props.appName}-files-${cdk.Aws.ACCOUNT_ID}-${cdk.Aws.REGION}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: props.removalPolicy ?? cdk.RemovalPolicy.RETAIN,
      autoDeleteObjects: props.removalPolicy === cdk.RemovalPolicy.DESTROY,
      lifecycleRules: [
        {
          id: 'ExpireTemporaryArtifacts',
          enabled: true,
          prefix: 'temp/',
          expiration: cdk.Duration.days(14),
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(7),
        },
        {
          id: 'ExpireOldNoncurrentVersions',
          enabled: true,
          noncurrentVersionExpiration: cdk.Duration.days(30),
        },
      ],
      cors: [
        {
          allowedMethods: [
            s3.HttpMethods.GET,
            s3.HttpMethods.PUT,
            s3.HttpMethods.POST,
            s3.HttpMethods.HEAD,
          ],
          allowedOrigins: ['*'],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
    });
  }

  public grantRead(grantee: iam.IGrantable): iam.Grant {
    return this.bucket.grantRead(grantee);
  }

  public grantWrite(grantee: iam.IGrantable): iam.Grant {
    return this.bucket.grantWrite(grantee);
  }

  public grantReadWrite(grantee: iam.IGrantable): iam.Grant {
    return this.bucket.grantReadWrite(grantee);
  }
}
