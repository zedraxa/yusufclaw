# YusufClaw Infrastructure Progress Report

**Date**: 2026-10-16
**Package**: `@yusufclaw/infra` (`/home/yusuf/projects/yusufclaw-infra`)
**AWS Profile**: `yusufclaw`
**AWS Region**: `us-east-1`
**Status**: Completed & Verified with `cdk synth` (Not deployed per Phase 1 instructions)

---

## 1. Executive Summary

The AWS CDK (TypeScript) infrastructure package for **YusufClaw** has been designed, implemented, tested, and synthesized. It sets up serverless and containerized AWS resources tailored to a personal AI agent with a strict **$50 USD monthly spending cap**.

All requirements from `SPEC.md` and the user prompt have been fulfilled:
1. **API Gateway Webhook -> Lambda**: Regional REST API and ARM64 Node.js 20 Lambda for Telegram updates.
2. **DynamoDB Memory Table**: Single-table on-demand billing, composite keys (`pk`, `sk`), `GSI1`, PITR, and TTL.
3. **S3 Files Bucket**: Encrypted, SSL enforced, private, versioned, with automatic lifecycle expiration for scratch data.
4. **ECS Fargate Heavy Jobs**: Cluster, task definition (1 vCPU, 2GB RAM), and custom IAM roles.
5. **Bedrock Invoke Permissions**: IAM policy supporting foundation models and cross-region inference profiles.
6. **Monthly $50 USD AWS Budget**: Multi-tier alert thresholds (50%, 80%, 100% actual, 100% forecasted) routed to SNS and email.
7. **Verification**: 100% pass rate across Jest unit tests and error-free `cdk synth` using profile `yusufclaw` in `us-east-1`.

---

## 2. Key Architecture Decisions & Budget Strategy

### The $50 Monthly Budget Constraint
A standard AWS NAT Gateway costs **~$32.40/month** ($0.045/hr) plus data processing fees. Running two NAT Gateways across two Availability Zones would cost **~$65/month**, immediately breaching the entire $50/month budget before running any compute workloads.

**Solution**:
- The VPC uses `natGateways: 0` with public subnets.
- Fargate heavy-job tasks run with `assignPublicIp: true`.
- Containers have direct egress to public endpoints (AWS Bedrock, Telegram API, tool webhooks) at **$0 NAT idle cost**.
- DynamoDB uses `PAY_PER_REQUEST` (On-Demand) billing: zero idle cost when the agent is inactive.
- Telegram webhook Lambda uses AWS Graviton `arm64` architecture (cheaper per ms than x86_64).
- S3 files bucket has lifecycle policies expiring temporary files (`temp/`) after 14 days and noncurrent versions after 30 days.

---

## 3. Implemented Components & Directory Structure

```
yusufclaw-infra/
├── bin/
│   └── infra.ts                     # CDK App entrypoint (sets us-east-1, tags: Project, Budget, Owner)
├── lib/
│   ├── config.ts                    # Global constants, default configuration, interfaces
│   ├── yusufclaw-infra-stack.ts     # Main stack assembling all constructs and CloudFormation outputs
│   └── constructs/
│       ├── database.ts              # DynamoDB memory table (on-demand, TTL, GSI1, PITR)
│       ├── storage.ts               # S3 files bucket (SSE, SSL enforcement, lifecycle rules)
│       ├── bedrock-iam.ts           # Bedrock invoke IAM policies (foundation models + inference profiles)
│       ├── heavy-jobs.ts            # ECS Fargate cluster, task definition, VPC (0 NAT), execution/task roles
│       ├── telegram-webhook.ts      # API Gateway REST API + Lambda handler
│       └── cost-budget.ts           # AWS Budgets $50 limit + SNS alert topic
├── lambda/
│   └── telegram-webhook/
│       ├── index.ts                 # TypeScript handler for Telegram updates & health checks
│       └── types.ts                 # Typed Telegram update interfaces
├── test/
│   └── yusufclaw-infra.test.ts      # 6 comprehensive CDK assertion test suites
├── cdk.json                         # Configured with profile "yusufclaw", ts-node runner, context flags
├── package.json                     # Dependencies: aws-cdk-lib, constructs, typescript, jest
├── tsconfig.json                    # Strict ES2022 / CommonJS configuration
├── README.md                        # Complete architecture and usage guide
└── PROGRESS.md                      # This report
```

---

## 4. Verification Results

### 1. TypeScript Compilation
- Command: `pnpm run build` (`tsc`)
- Result: **Clean exit (code 0)**. Zero type errors.

### 2. Unit Tests (`@aws-cdk/assertions`)
- Command: `pnpm test` (`jest`)
- Result: **6 passed / 6 total**:
  - `DynamoDB Memory Table is provisioned with On-Demand billing, TTL, and GSI` (Passed)
  - `S3 Files Bucket blocks public access, enforces SSL, and has lifecycle rules` (Passed)
  - `Bedrock IAM permissions exist for foundation models and inference profiles` (Passed)
  - `ECS Fargate heavy jobs cluster and task definition with 0 NAT Gateways` (Passed)
  - `Telegram Webhook API Gateway and ARM64 Node 20 Lambda` (Passed)
  - `AWS Budget establishes strict 50 USD monthly cap with multi-tier alerts` (Passed)

### 3. CDK Synthesis
- Command: `pnpm cdk synth --profile yusufclaw`
- Result: **Clean synthesis (code 0)**. Generated valid CloudFormation template with parameters, resources, and outputs.

---

## 5. CloudFormation Outputs for Application Integration

| Export Name | Target Resource | Description |
|---|---|---|
| `yusufclaw-telegram-webhook-url` | `TelegramWebhookTelegramApi` | Webhook URL for Telegram `setWebhook` |
| `yusufclaw-dynamodb-table-name` | `DatabaseMemoryTable` | Table name for agent memory & turn storage |
| `yusufclaw-files-bucket-name` | `StorageFilesBucket` | S3 bucket name for files, media, and tools |
| `yusufclaw-ecs-cluster-name` | `HeavyJobsCluster` | ECS Cluster for background tasks |
| `yusufclaw-fargate-task-def-arn` | `HeavyJobsTaskDef` | Task Definition ARN for `ecs:RunTask` dispatch |
| `yusufclaw-budget-alert-topic-arn` | `CostBudgetBudgetAlertTopic`| SNS Topic ARN for budget breach alerts |

---

## 6. Next Steps (For Deployment & App Integration)

1. **Telegram Bot Token**:
   Store the Telegram bot token in AWS Secrets Manager:
   ```bash
   aws secretsmanager create-secret \
     --name yusufclaw/telegram/bot-token \
     --secret-string "YOUR_TELEGRAM_BOT_TOKEN" \
     --profile yusufclaw \
     --region us-east-1
   ```
2. **Phase 2 Deployment** (When authorized by user):
   ```bash
   pnpm cdk bootstrap --profile yusufclaw
   pnpm cdk deploy --profile yusufclaw
   ```
3. **Register Webhook with Telegram**:
   ```bash
   curl -F "url=<TelegramWebhookEndpoint>" \
     "https://api.telegram.org/bot<TOKEN>/setWebhook"
   ```
