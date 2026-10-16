# YusufClaw — open-source personal AI agent (OpenClaw / Grok Bot style)
Goal: one always-on agent Yusuf can command from anywhere (Telegram, WhatsApp later, web UI, CLI, email).
Stack: TypeScript/Node 20 monorepo (pnpm). Packages:
- gateway: channel adapters (telegram first, web chat, CLI) -> unified message bus
- core: agent loop with tool calling, memory (SQLite locally / DynamoDB on AWS), skills folder, scheduled routines (cron)
- models: provider layer — AWS Bedrock (default, profile `yusufclaw`, us-east-1), local Ollama, OpenAI-compatible
- tools: shell (sandboxed, approval for dangerous cmds), files, web fetch, browser (playwright), delegate-to-local-agents (agy, codex CLI)
- infra: AWS CDK — Lambda/ECS Fargate for heavy jobs, S3 for files, DynamoDB memory, API Gateway webhook for Telegram
Security: secrets via env/.env (never committed), allowlist of user IDs, approval step for destructive actions.
Deliver in phases. Phase 1: monorepo skeleton, core loop, Bedrock+Ollama providers, CLI + Telegram adapters, SQLite memory, README, tests. Do not deploy to AWS yet.
