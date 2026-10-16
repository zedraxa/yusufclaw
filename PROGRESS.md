# Phase 1 progress

Phase 1 of SPEC.md is complete. No AWS resources were deployed.

## Built

- Node 20 / TypeScript / pnpm workspace with `gateway`, `core`, `models`, `tools`, and reserved `infra` packages, reproducible lockfile, and CI checks.
- Agent loop with multi-step tool calling, JSON Schema validation, tool-result feedback, step/batch limits, cancellation, sanitized errors, and explicit approval hooks for future destructive tools. The included `current_time` tool is read-only.
- Bedrock Converse provider using profile `yusufclaw` and region `us-east-1` by default, configurable model/inference profile, output limit, timeout, and adaptive retries.
- Ollama chat provider with conversation and tool-call translation, response validation, timeout, and output limit.
- Shared authorized message bus; interactive and one-shot CLI; Telegram long polling with private-chat user allowlisting, persistent offsets, retry/backoff, reply chunking, and shutdown handling.
- SQLite conversation history with WAL, per-channel/user/conversation isolation, completed-turn persistence, replayed-request reply reuse, and `/reset` support. Turns within a session are serialized inside the process.
- README, `.env.example`, ignored secrets/data/build output, and reserved trusted-skills directory.

## How to run

Use Node 20.19+ and pnpm 9.15.9. From this folder:

```sh
pnpm install --frozen-lockfile
cp .env.example .env
```

For local inference, start Ollama, run `ollama pull llama3.2`, and set `MODEL_PROVIDER=ollama` in `.env`. For Bedrock, configure the `yusufclaw` AWS profile and set `BEDROCK_MODEL_ID` to an enabled Converse/tool-use model or inference profile.

```sh
pnpm cli --message "What time is it in UTC?"
pnpm cli
pnpm cli --session planning --message "Help me plan tomorrow"
```

For Telegram, also set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`, then run `pnpm telegram`. Use one poller per bot and remove any pre-existing webhook. See README.md for full configuration and troubleshooting notes.

```sh
pnpm check
```

## Verification

Verified with Node v20.20.2 and pnpm 9.15.9:

- `pnpm install --frozen-lockfile` passed.
- `pnpm check` passed: all application/test TypeScript checks and **29 tests in 4 files**.
- The integration test launches the compiled CLI against a real local HTTP fixture, completes a clock-tool round trip, restores SQLite history in a second process, and verifies reset.
- Unit tests cover provider payloads/errors, memory reopen/isolation, approvals and invalid tools, bounded loops, concurrent turns, authorization, Telegram polling offsets/replay/rate-limit retry, cancellation, and Unicode-safe reply chunks.
- `pnpm cli --help` passed.

The host's default Node was v18.19.1, so verification used an isolated Node 20/pnpm 9 toolchain without changing the global installation. Live AWS account/model access, a running Ollama model, and Telegram delivery were not exercised; external transports were mocked or served by the local fixture.

## What's next

The spec names only Phase 1's delivery boundary. Suggested next work for its broader roadmap:

1. Add sandboxed shell and scoped file tools with durable action records, idempotency, and channel-specific approvals before enabling destructive operations.
2. Add web fetching, Playwright browser tools, and local `agy`/Codex CLI delegation with resource and network limits.
3. Implement trusted skill loading and scheduled routines; add an OpenAI-compatible provider and web chat.
4. Improve long-term memory with token-aware context selection/summarization, retention policies, and migration/version support.
5. Add a durable Telegram outbox and cross-process session coordination. Current delivery is at least once; send/offset crashes or partial chunk failures can duplicate replies.
6. Design and validate CDK infrastructure, DynamoDB memory, S3 files, and webhook/heavy-job hosting before a separately authorized AWS deployment. WhatsApp and email remain later channels.

Current operational limits: memory is plaintext on the local host, history selection is turn-count based, skill files are not executed, and there are no shell/file/browser/delegation tools or cloud resources. The approval interface is not a sandbox. Run one writer per conversation; Phase 1's session queue does not coordinate separate processes.
