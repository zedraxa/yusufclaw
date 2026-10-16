# YusufClaw AWS Infrastructure Python CDK Port (PY-INFRA)

> **Branch:** `py-infra`  
> **Status:** VALIDATE ONLY — Verified via `cdk synth` and pytest assertions; **NEVER DEPLOYED**.  
> **Target Region:** `us-east-1`  
> **Target Profile:** `yusufclaw`  
> **Monthly Budget Limit:** `$50 USD`

---

## 1. Executive Summary

As part of the YusufClaw transition to Python 3.12 (`docs/ARCHITECTURE-v2.md`), the cloud infrastructure has been ported from TypeScript AWS CDK (`packages/infra`) to a standalone, modular Python AWS CDK v2 package located in `infra_py/`.

The Python CDK implementation synthesizes the exact same 49 CloudFormation resources and 10 stack outputs/exports with 100% resource intent and architectural parity, while respecting all project safety constraints (zero NAT Gateways for budget protection, on-demand serverless pricing, and least-privilege IAM).

---

## 2. Architecture & Design Rationale

```
                      [ Telegram Client ]
                               │
                               ▼ HTTPS POST (Webhook)
                [ Amazon API Gateway (Regional REST) ]
                               │
                               ▼
               [ AWS Lambda (Python 3.12, ARM64) ]
                   │            │           │
       ┌───────────┘            │           └───────────┐
       ▼                        ▼                       ▼
[ Amazon DynamoDB ]      [ Amazon S3 ]         [ Amazon Bedrock ]
 (Memory & Turns)       (Files & Media)      (Claude 3.5 Sonnet /
  On-Demand / TTL      SSL, Encrypted,       Foundation Models /
  Single-Table          Lifecycle Rules      Inference Profiles)
       │                        ▲                       ▲
       │                        │                       │
       └───────────┐            │           ┌───────────┘
                   ▼            │           │
           [ ECS Fargate Heavy Jobs Cluster ]
             (Tasks in Public Subnets, 0 NAT Gateways)
```

In parallel, **AWS Budgets** continuously monitors total account spending against a strict **$50 USD monthly limit**, dispatching proactive notifications through an Amazon SNS topic and email.

---

## 3. Provisioned Subsystems & Construct Comparison

### 3.1 DynamoDB Single-Table Memory (`infra_py/constructs/database.py`)
- **Table Name**: `yusufclaw-memory`
- **Billing Mode**: `PAY_PER_REQUEST` (On-Demand) — **$0 idle cost**, ideal for personal agent workloads.
- **Partition Key**: `pk` (String, e.g. `SESSION#<sessionId>`, `STATE#<key>`).
- **Sort Key**: `sk` (String, e.g. `TURN#<timestamp>#<requestId>`, `METADATA`).
- **Global Secondary Index**: `GSI1` (`gsi1pk`, `gsi1sk`, projection `ALL`) for inverted queries and request deduplication.
- **Reliability & Hygiene**: Point-in-Time Recovery (PITR) enabled; TTL enabled on `ttl` attribute for automatic ephemeral session expiration.
- **Encryption & Removal**: AWS-managed encryption; `RemovalPolicy.RETAIN` by default.

### 3.2 S3 File & Artifact Storage (`infra_py/constructs/storage.py`)
- **Bucket Name**: `yusufclaw-files-${AWS::AccountId}-${AWS::Region}`
- **Security**:
  - `BlockPublicAccess.BLOCK_ALL` (all 4 public access block flags).
  - SSE-S3 managed server-side encryption (`AES256`).
  - Strict SSL enforcement via bucket policy denying all `s3:*` actions when `aws:SecureTransport: false`.
  - Object versioning enabled.
- **Cost-Optimized Lifecycle**:
  - `temp/` scratch files expire automatically after 14 days.
  - Noncurrent versions expire after 30 days.
  - Incomplete multipart uploads abort after 7 days.
- **Integration**: CORS enabled for GET, PUT, POST, HEAD from agent tools.

### 3.3 Amazon Bedrock Least-Privilege IAM (`infra_py/constructs/bedrock_iam.py`)
- **Actions Allowed**:
  - `bedrock:InvokeModel`
  - `bedrock:InvokeModelWithResponseStream`
  - `bedrock:GetFoundationModel`
  - `bedrock:ListFoundationModels`
  - `bedrock:GetInferenceProfile`
  - `bedrock:ListInferenceProfiles`
  - `bedrock:ApplyGuardrail`
- **Resource Scope**:
  - Foundation models: `arn:aws:bedrock:*::foundation-model/*` (or scoped IDs)
  - Regional & cross-region inference profiles: `arn:${Partition}:bedrock:*:${AccountId}:inference-profile/*`
  - Application inference profiles: `arn:${Partition}:bedrock:*:${AccountId}:application-inference-profile/*`
  - Guardrails: `arn:${Partition}:bedrock:*:${AccountId}:guardrail/*`
- **Managed Policy**: `BedrockInvokeManagedPolicy`, attachable to Lambda and Fargate task roles.

### 3.4 ECS Fargate Heavy Jobs (0 NAT Gateways) (`infra_py/constructs/heavy_jobs.py`)
- **Cost Protection Architecture**:
  - Standard AWS NAT Gateways cost ~$32.40/month each ($64.80 for 2 AZs), which alone exceeds the entire $50 monthly budget on idle VPC routing.
  - To eliminate idle networking charges, the VPC provisions public subnets with **0 NAT Gateways**.
  - Fargate tasks run with `assignPublicIp: true`, giving containers direct internet egress to Bedrock, Telegram, and external APIs with **$0 idle NAT charges**.
- **Specs**: 1024 CPU units (1 vCPU), 2048 MB RAM.
- **Security Group**: Egress-only outbound communication (`allow_all_outbound=True`).
- **Cluster & Logging**: ECS cluster with Container Insights v2 disabled (saving CloudWatch metric fees); dedicated CloudWatch Log Group (`/ecs/yusufclaw-heavy-jobs`) with 30-day retention.
- **IAM Separation**:
  - `execution_role`: Minimal ECR image pull and CloudWatch logging permissions.
  - `task_role`: Application permissions granted for DynamoDB read/write, S3 read/write, and Bedrock invocation.
- **Dispatch Permission**: `grant_run_task(grantee)` grants `ecs:RunTask` (conditional on cluster ARN) and `iam:PassRole` for execution and task roles.

### 3.5 API Gateway & Telegram Webhook Lambda (`infra_py/constructs/telegram_webhook.py`)
- **Regional REST API**: `yusufclaw-telegram-api` with `prod` stage.
- **Throttling Protection**: 50 req/sec rate limit with 100 burst capacity to prevent surprise bill spikes and DDoS.
- **Endpoints**:
  - `POST /webhook/telegram`: Telegram webhook updates.
  - `GET /webhook/telegram`: Health check endpoint.
- **Lambda Handler (`infra_py/lambda/telegram_webhook/index.py`)**:
  - Runtime: Python 3.12 (`arm64` Graviton architecture for maximum price-performance).
  - Timeout: 60s, RAM: 512MB.
  - Validates `x-telegram-bot-api-secret-token` when `TELEGRAM_SECRET_HEADER` is set.
  - Returns fast 200 OK responses to comply with Telegram webhook delivery SLAs.
  - Gracefully handles malformed payloads (returns 200 `{"ok": false}`) to prevent Telegram retry storms.
  - Permissions: DynamoDB, S3, Bedrock, and ECS `RunTask` / `PassRole`.

### 3.6 AWS Budgets $50/mo Spending Cap (`infra_py/constructs/cost_budget.py`)
- **Budget Name**: `yusufclaw-monthly-50-usd-budget`
- **Limit**: `$50 USD` monthly recurring budget (`COST` type).
- **Inclusions**: Recurring charges, taxes, support, discounts, subscriptions; credits excluded so true spend is monitored.
- **Tiered Multi-Channel Alerts**:
  - **50% Actual ($25)**: Early warning of monthly usage.
  - **80% Actual ($40)**: Threshold alert for pacing workload.
  - **100% Actual ($50)**: Budget cap alert.
  - **100% Forecasted ($50)**: Proactive warning triggered before month end if spending trajectory will exceed $50.
- **Channels**:
  - Dedicated Amazon SNS Topic (`yusufclaw-budget-alerts`) with topic policy permitting `budgets.amazonaws.com` publishing.
  - Email subscriber configured via `alert_email` (default: `alerts@yusufclaw.local`).

---

## 4. CloudFormation Template Equivalence

Both the TypeScript CDK stack and the Python CDK stack synthesize identical CloudFormation templates:

| Resource Type | TS Stack Count | Python Stack Count | Match |
|---|---|---|---|
| `AWS::DynamoDB::Table` | 1 | 1 | 100% |
| `AWS::S3::Bucket` | 1 | 1 | 100% |
| `AWS::S3::BucketPolicy` | 1 | 1 | 100% |
| `AWS::IAM::ManagedPolicy` | 1 | 1 | 100% |
| `AWS::IAM::Role` | 4 | 4 | 100% |
| `AWS::IAM::Policy` | 3 | 3 | 100% |
| `AWS::EC2::VPC` | 1 | 1 | 100% |
| `AWS::EC2::Subnet` | 2 | 2 | 100% |
| `AWS::EC2::RouteTable` | 2 | 2 | 100% |
| `AWS::EC2::Route` | 2 | 2 | 100% |
| `AWS::EC2::SubnetRouteTableAssociation` | 2 | 2 | 100% |
| `AWS::EC2::InternetGateway` | 1 | 1 | 100% |
| `AWS::EC2::VPCGatewayAttachment` | 1 | 1 | 100% |
| `AWS::EC2::SecurityGroup` | 1 | 1 | 100% |
| `AWS::ECS::Cluster` | 1 | 1 | 100% |
| `AWS::ECS::TaskDefinition` | 1 | 1 | 100% |
| `AWS::Lambda::Function` | 2 | 2 | 100% |
| `AWS::Lambda::Permission` | 4 | 4 | 100% |
| `AWS::Logs::LogGroup` | 2 | 2 | 100% |
| `AWS::ApiGateway::RestApi` | 1 | 1 | 100% |
| `AWS::ApiGateway::Resource` | 2 | 2 | 100% |
| `AWS::ApiGateway::Method` | 5 | 5 | 100% |
| `AWS::ApiGateway::Deployment` | 1 | 1 | 100% |
| `AWS::ApiGateway::Stage` | 1 | 1 | 100% |
| `AWS::SNS::Topic` | 1 | 1 | 100% |
| `AWS::SNS::TopicPolicy` | 1 | 1 | 100% |
| `AWS::SNS::Subscription` | 1 | 1 | 100% |
| `AWS::Budgets::Budget` | 1 | 1 | 100% |
| `Custom::VpcRestrictDefaultSG` | 1 | 1 | 100% |
| `AWS::CDK::Metadata` | 1 | 1 | 100% |
| **Total Resources** | **49** | **49** | **100%** |

### CloudFormation Outputs & Exports

| Output Key | Export Name | Description |
|---|---|---|
| `TelegramWebhookEndpoint` | `yusufclaw-telegram-webhook-url` | Public URL for Telegram `setWebhook` |
| `MemoryTableName` | `yusufclaw-dynamodb-table-name` | DynamoDB Memory table name |
| `MemoryTableArn` | *(none)* | DynamoDB Memory table ARN |
| `FilesBucketName` | `yusufclaw-files-bucket-name` | S3 Files bucket name |
| `FilesBucketArn` | *(none)* | S3 Files bucket ARN |
| `HeavyJobsClusterName` | `yusufclaw-ecs-cluster-name` | ECS Fargate Cluster name |
| `HeavyJobsTaskDefinitionArn` | `yusufclaw-fargate-task-def-arn` | ECS Task Definition ARN |
| `BudgetAlertTopicArn` | `yusufclaw-budget-alert-topic-arn` | SNS Budget Alert Topic ARN |
| `BedrockManagedPolicyArn` | *(none)* | IAM Managed Policy ARN for Bedrock |

---

## 5. Development & Verification Guide

### 5.1 Running Offline Unit Tests
All unit tests run completely offline without contacting AWS:

```bash
cd infra_py
uv run pytest
```
Result: **14 passed in 11.91s** (covers all constructs, assertion matchers, config overrides, and Lambda handler scenarios).

### 5.2 Linting & Formatting
```bash
cd infra_py
uv run ruff check .
```
Result: **All checks passed! (0 errors, 0 warnings)**

### 5.3 Strict Type Checking
```bash
cd infra_py
uv run mypy infra_py tests lambda
```
Result: **Success: no issues found in 13 source files (0 errors)**

### 5.4 Synthesizing CloudFormation (VALIDATE ONLY)
To synthesize the CloudFormation template:

```bash
# Option A: Using AWS CDK CLI
cd infra_py
PATH="$(pwd)/.venv/bin:$PATH" npx cdk synth --profile yusufclaw

# Option B: Direct Python execution
cd infra_py
uv run python app.py
```

If AWS credentials for profile `yusufclaw` are not configured on a machine, synthesis can still run offline with mock context:
```bash
cd infra_py
CDK_DEFAULT_REGION=us-east-1 CDK_DEFAULT_ACCOUNT=123456789012 uv run python app.py
```

> [!CAUTION]
> **NEVER run `cdk deploy` or `aws cloudformation deploy`.**
> Per project rules and roadmap P0/P1, cloud deployment is strictly prohibited and reserved for future authorized releases.

---

## 6. Project Layout

```
infra_py/
├── cdk.json                     # CDK CLI configuration (profile: yusufclaw, context flags)
├── pyproject.toml               # Python package configuration, dependencies, and lint rules
├── requirements.txt             # Pip dependency list
├── requirements-dev.txt         # Dev tools dependency list
├── .gitignore                   # Ignored build, cdk.out, and cache files
├── README.md                    # Subsystem readme
├── app.py                       # CDK App entrypoint and stack synthesizer
├── infra_py/
│   ├── __init__.py              # Package exports
│   ├── config.py                # Strongly-typed YusufClawConfig dataclass
│   ├── stack.py                 # YusufClawInfraStack orchestrator and outputs
│   └── constructs/
│       ├── __init__.py          # Construct exports
│       ├── database.py          # DynamoDB Memory table construct
│       ├── storage.py           # S3 Files bucket construct
│       ├── bedrock_iam.py       # Bedrock IAM policy construct
│       ├── heavy_jobs.py        # ECS Fargate 0 NAT heavy jobs construct
│       ├── telegram_webhook.py  # API Gateway + Lambda webhook construct
│       └── cost_budget.py       # AWS Budgets 50 USD monthly cap construct
├── lambda/
│   └── telegram_webhook/
│       └── index.py             # Python 3.12 ARM64 Telegram webhook Lambda handler
└── tests/
    ├── __init__.py
    └── test_infra_stack.py      # 14 unit tests with CDK assertions and Lambda tests
```
