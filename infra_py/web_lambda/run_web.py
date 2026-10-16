"""Lambda container entrypoint: load the web auth token from Secrets Manager, start the gateway."""

import asyncio
import os

import boto3  # type: ignore[import-not-found]


def main() -> None:
    secret_arn = os.environ["WEB_AUTH_SECRET_ARN"]
    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    os.environ["WEB_AUTH_TOKEN"] = client.get_secret_value(SecretId=secret_arn)["SecretString"]
    os.chdir("/app")

    from yusufclaw.gateway.main import async_main  # type: ignore[import-not-found]

    asyncio.run(async_main(["web"]))


if __name__ == "__main__":
    main()
