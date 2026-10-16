> Monorepo import: run package commands from `packages/infra`, or use
> `pnpm --filter @yusufclaw/infra <command>` at the repository root. Install
> dependencies from the root workspace. This import has not deployed AWS resources.
> Budget alerts do not enforce a hard spending cap. The webhook currently only
> acknowledges updates; agent integration and deployment packaging remain follow-up work.

# @yusufclaw/infra

AWS CDK (TypeScript) Infrastructure package for **YusufClaw** — personal AI agent.

Target AWS Region: `us-east-1`
Target AWS Profile: `yusufclaw`
Monthly Budget Cap: `$50 USD`

---

## 🏗️ Architecture Overview

```
                      [ Telegram Client ]
                               │
                               ▼ HTTPS POST (Webhook)
               [ Amazon API Gateway (Regional REST) ]
                               │
                               ▼
               [ AWS Lambda (Node.js 20, ARM64) ]
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

## 📦 Provisioned Resources

### 1. API Gateway & Telegram Webhook Lambda
- **Endpoint**: Regional REST API Gateway at `/webhook/telegram` (POST and GET).
- **Throttling**: 50 req/sec rate limit with 100 burst capacity to safeguard against traffic spikes and surprise costs.
- **Compute**: AWS Lambda running on `nodejs20.x` with `arm64` (AWS Graviton) for high price-performance.
- **Security**: Validates optional `x-telegram-bot-api-secret-token` header. Fast 200 OK responses to comply with Telegram webhook delivery SLAs.
- **Observability**: CloudWatch Log Group with 30-day automatic retention.

### 2. DynamoDB Memory Table (`yusufclaw-memory`)
- **Billing Mode**: `PAY_PER_REQUEST` (On-Demand) — **0 idle cost**, ideal for personal agent workloads.
- **Schema**:
  - Partition Key: `pk` (String, e.g. `SESSION#<sessionId>`, `STATE#<key>`)
  - Sort Key: `sk` (String, e.g. `TURN#<timestamp>#<requestId>`, `METADATA`)
  - Global Secondary Index: `GSI1` (`gsi1pk`, `gsi1sk`, projection `ALL`)
- **Features**: Point-in-time recovery (PITR) enabled; TTL enabled on `ttl` attribute for ephemeral session cleanup.

### 3. S3 Files Storage Bucket (`yusufclaw-files-<account>-<region>`)
- **Security**: Strict public access block (`BlockPublicAccess.BLOCK_ALL`), AWS-managed SSE encryption (`AES256`), and enforced SSL (`aws:SecureTransport: false` deny policy).
- **Lifecycle Rules**:
  - `temp/` scratch files expire automatically after 14 days.
  - Noncurrent versions expire after 30 days.
  - Incomplete multipart uploads abort after 7 days.
- **Integration**: Pre-configured CORS for agent tool interactions.

### 4. ECS Fargate Task Definition for Heavy Jobs
- **VPC & Budget Protection**: Standard AWS NAT Gateways cost ~$32.40/month each (exceeding the entire $50 monthly budget before any workload runs). The VPC uses `natGateways: 0` and public subnets. Fargate tasks run with `assignPublicIp: true`, giving containers direct internet egress for tool execution, browser automation (Playwright), and API calls with **zero idle NAT charges**.
- **Task Specs**: 1024 CPU units (1 vCPU), 2048 MB RAM.
- **IAM**: Separate execution role (ECR/logging) and task role with granular permissions for DynamoDB, S3, and Bedrock.
- **Dispatch**: Telegram Lambda is granted `ecs:RunTask` and `iam:PassRole` to spawn heavy jobs asynchronously.

### 5. Amazon Bedrock IAM Permissions
- **Actions**: `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:GetFoundationModel`, `bedrock:ListFoundationModels`, `bedrock:GetInferenceProfile`, `bedrock:ListInferenceProfiles`, `bedrock:ApplyGuardrail`.
- **Resources**: Covers both foundation models (`arn:aws:bedrock:*::foundation-model/*`) and cross-region inference profiles (`arn:aws:bedrock:<region>:<account>:inference-profile/*`).
- **Target Identities**: Managed policy attached to both Telegram Lambda and Fargate heavy job task roles.

### 6. Monthly 50 USD AWS Budget
- **Limit**: `$50 USD` monthly recurring budget.
- **Notifications**:
  - `50%` ($25) Actual spend
  - `80%` ($40) Actual spend
  - `100%` ($50) Actual spend
  - `100%` ($50) Forecasted spend (early warning before month end)
- **Channels**: Amazon SNS Topic (`yusufclaw-budget-alerts`) with AWS Budgets publishing policy and email subscribers.

---

## 🚀 Development & Verification

### Prerequisites
- Node.js >= 18 (Node 20 recommended)
- `pnpm` >= 9
- AWS CLI configured with profile `yusufclaw`

### Commands

```bash
# Install dependencies
pnpm install

# Compile TypeScript
pnpm run build

# Run unit tests (Jest + CDK Assertions)
pnpm test

# Synthesize CloudFormation template (verifies template generation without deploying)
pnpm cdk synth --profile yusufclaw
```

> [!NOTE]
> Per specification, **DO NOT deploy** in Phase 1. Synthesis and assertions verify structural correctness.

---

## 📄 Stack CloudFormation Exports

| Export Name | Description |
|---|---|
| `yusufclaw-telegram-webhook-url` | Webhook URL for Telegram `setWebhook` |
| `yusufclaw-dynamodb-table-name` | DynamoDB Memory Table Name |
| `yusufclaw-files-bucket-name` | S3 Files Bucket Name |
| `yusufclaw-ecs-cluster-name` | ECS Fargate Cluster Name |
| `yusufclaw-fargate-task-def-arn` | ECS Fargate Task Definition ARN |
| `yusufclaw-budget-alert-topic-arn` | SNS Budget Alert Topic ARN |
