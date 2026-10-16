"""DynamoDB Single-Table Memory construct for YusufClaw personal AI agent."""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from constructs import Construct


class YusufClawDatabase(Construct):
    """DynamoDB Single-Table Memory storage for YusufClaw agent.

    Designed for serverless pay-per-request billing (0 idle cost) to respect
    the $50 monthly budget.

    Schema:
    - PK: Partition Key (e.g. `SESSION#<sessionId>`, `STATE#<key>`)
    - SK: Sort Key (e.g. `TURN#<timestamp>#<requestId>`, `METADATA`)
    - GSI1PK / GSI1SK: Secondary index for querying by request ID or turn types.
    - TTL: Unix epoch timestamp for automatic ephemeral session expiration.
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

        self.table = dynamodb.Table(
            self,
            "MemoryTable",
            table_name=f"{app_name}-memory",
            partition_key=dynamodb.Attribute(
                name="pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="sk",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=removal_policy or cdk.RemovalPolicy.RETAIN,
        )

        # Add GSI for inverted lookups / request filtering
        self.table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="gsi1pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="gsi1sk",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

    def grant_read_data(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant read permissions on the memory table to grantee."""
        return self.table.grant_read_data(grantee)

    def grant_write_data(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant write permissions on the memory table to grantee."""
        return self.table.grant_write_data(grantee)

    def grant_read_write_data(self, grantee: iam.IGrantable) -> iam.Grant:
        """Grant read and write permissions on the memory table to grantee."""
        return self.table.grant_read_write_data(grantee)
