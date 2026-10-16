"""S3 Storage construct for YusufClaw personal AI agent."""

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class YusufClawStorage(Construct):
    """S3 Storage for YusufClaw agent files, attachments, tool scratch spaces, and media.

    Configured with SSL enforcement, strict public access blocking, versioning,
    and cost-effective lifecycle expiration for temporary files.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        app_name: str,
        removal_policy: cdk.RemovalPolicy | None = None,
    ) -> None:
        super().__init__(scope, id_)

        policy = removal_policy or cdk.RemovalPolicy.RETAIN

        self.bucket = s3.Bucket(
            self,
            "FilesBucket",
            bucket_name=f"{app_name}-files-{cdk.Aws.ACCOUNT_ID}-{cdk.Aws.REGION}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=policy,
            auto_delete_objects=policy == cdk.RemovalPolicy.DESTROY,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireTemporaryArtifacts",
                    enabled=True,
                    prefix="temp/",
                    expiration=cdk.Duration.days(14),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(7),
                ),
                s3.LifecycleRule(
                    id="ExpireOldNoncurrentVersions",
                    enabled=True,
                    noncurrent_version_expiration=cdk.Duration.days(30),
                ),
            ],
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.POST,
                        s3.HttpMethods.HEAD,
                    ],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3000,
                ),
            ],
        )

    def grant_read(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant read permissions on the files bucket to grantee."""
        return self.bucket.grant_read(grantee)

    def grant_write(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant write permissions on the files bucket to grantee."""
        return self.bucket.grant_write(grantee)

    def grant_read_write(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant read and write permissions on the files bucket to grantee."""
        return self.bucket.grant_read_write(grantee)
