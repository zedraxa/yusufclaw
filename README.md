# YusufClaw

A local-first personal AI agent. Phase 1 provides a TypeScript/pnpm monorepo, a tool-calling agent loop, AWS Bedrock and Ollama providers, persistent SQLite conversations, and CLI and Telegram channels. No AWS infrastructure is provisioned.

## Requirements and installation

Use Node **20.19 or newer** (Node 20 is the tested target) and **pnpm 9.15.9**. With nvm and Corepack installed:

```sh
nvm install
nvm use
corepack enable
corepack prepare pnpm@9.15.9 --activate
pnpm install --frozen-lockfile
cp .env.example .env
```

`better-sqlite3` installs a native module. If a prebuilt binary is unavailable, install Python 3, make, and a C++ compiler. Reinstall dependencies when changing Node major versions. Run commands from the repository root so `.env` and relative data paths resolve consistently.

## Run with Ollama

Install and start Ollama separately, then pull a model that supports tools:

```sh
ollama pull llama3.2
```

In `.env`, set `MODEL_PROVIDER=ollama`. The default endpoint is `http://127.0.0.1:11434`; change `OLLAMA_MODEL` if desired. A blank `BEDROCK_MODEL_ID` is accepted in Ollama mode.

```sh
pnpm cli --message "What is the current UTC time?"
pnpm cli
pnpm cli --session planning --message "Help me plan tomorrow"
```

Interactive CLI commands: `/help`, `/reset`, and `/exit`. One-shot calls accept `--message` (`-m`) and `--session` (`-s`). Noninteractive stdin requires `--message`. The default session persists between invocations. `/reset` deletes that conversation's stored turns; it does not erase other sessions.

## Run with Bedrock (default)

Configure AWS credentials for profile `yusufclaw` using your existing AWS authentication workflow. Set these values in `.env`:

```dotenv
MODEL_PROVIDER=bedrock
AWS_PROFILE=yusufclaw
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=your-enabled-model-or-inference-profile-id
```

Choose a model supporting Converse, system prompts, and tool use, available to your account and region. The AWS identity needs `bedrock:InvokeModel` on the selected model and any resources required by its inference profile. The SDK uses its credential provider chain with the named profile; credentials are not stored in this repository. Model selection is explicit because access and inference-profile requirements vary by account. Invocations incur normal Bedrock charges.

```sh
pnpm cli --message "Hello YusufClaw"
```

The provider uses Converse with an explicit output-token limit, adaptive retries, and a request timeout. See the official [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) and [Ollama chat API](https://docs.ollama.com/api/chat) for provider behavior.

## Run Telegram

Create a bot through Telegram's BotFather and set the token and your **numeric Telegram user ID**, not a username or group ID:

```dotenv
TELEGRAM_BOT_TOKEN=123456:your_real_token
TELEGRAM_ALLOWED_USER_IDS=123456789
```

```sh
pnpm telegram
```

Message the bot in a private chat. `/start`, `/help`, and `/reset` work without invoking a model. Multiple trusted users can be separated with commas. Both the adapter and bus enforce the allowlist; startup fails when the list is empty. Group messages, bot senders, non-text messages, and unauthorized users are ignored.

This adapter uses [Telegram long polling](https://core.telegram.org/bots/api#getupdates), so it needs outbound HTTPS and no public webhook. Remove any existing webhook before polling and run only one polling process per bot token. Authentication failures and competing pollers stop the process; transient errors retry with backoff and rate-limit hints. SIGINT/SIGTERM cancel requests and close memory. Use a process supervisor if you need automatic restarts.

Replies are sent as plain text and split into safe chunks. Polling offsets persist in SQLite. Replayed updates reuse completed replies rather than invoking the agent again. Delivery is **at least once**: a crash between sending a reply and saving the offset, or a partial chunk failure, can produce duplicate messages. This is not a transactional Telegram outbox.

## Configuration

| Variable | Default / meaning |
| --- | --- |
| `MODEL_PROVIDER` | `bedrock`, or `ollama` |
| `BEDROCK_MODEL_ID` | Required in Bedrock mode; no assumed model |
| `AWS_PROFILE`, `AWS_REGION` | `yusufclaw`, `us-east-1` |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `http://127.0.0.1:11434`, `llama3.2` |
| `MEMORY_PATH` | `.data/memory.sqlite` |
| `CLI_USER_ID` | `local`; identity for the trusted local CLI |
| `MAX_TOKENS` | `2048` output tokens |
| `MODEL_TIMEOUT_MS` | `120000` per model request |
| `MAX_AGENT_STEPS` | `8` model calls per turn |
| `HISTORY_TURNS` | `20` recent complete turns sent to the model |
| `TELEGRAM_BOT_TOKEN` | Required only for Telegram |
| `TELEGRAM_ALLOWED_USER_IDS` | Empty by default; Telegram refuses to start |

Existing environment variables take precedence over `.env`. Configuration is validated on startup. Failures exit nonzero; channel error messages intentionally omit raw SDK and HTTP errors, which can contain credentials or user input.

## Packages and execution

| Package | Responsibility |
| --- | --- |
| `@yusufclaw/models` | Shared message/tool contracts, Bedrock Converse and Ollama chat adapters |
| `@yusufclaw/tools` | Tool interface, approval hook, read-only `current_time` tool |
| `@yusufclaw/core` | Bounded agent loop, JSON-schema argument validation, SQLite memory |
| `@yusufclaw/gateway` | Authorized message bus, configuration, CLI and Telegram entry points |
| `@yusufclaw/infra` | Reserved package; no deployable resources in Phase 1 |
| `skills/` | Reserved for a future trusted skill loader |

Adapters normalize input into the message bus. The bus checks authorization and builds a session key from channel, user, and conversation. The core serializes turns per session within one process, loads recent complete turns, asks the model, validates and executes requested tools, and repeats until it gets a final response. All tool results are preserved with their call IDs. A successful turn is saved in one SQLite statement; a failed turn is not added to history. Model and tool failures cannot leave half a conversation in memory.

SQLite uses WAL and a busy timeout. The database file is created with mode 0600 and new parent directories with mode 0700. History is local plaintext and grows until reset; protect the host and backups. Context is bounded by complete turns, not tokens, so unusually large conversations may still exceed a model's context window. Session keys are intentionally isolated between CLI and Telegram. Run a single writer per conversation; the in-process queue is not a cross-process lock.

## Security and tool scope

Phase 1 ships only `current_time`. Shell execution, file modification, web fetching, browser control, and agent delegation are not enabled. The tool registry validates arguments against JSON Schema, rejects unknown tools, bounds tool batches and model steps, and sanitizes tool failures. Additional tools are trusted application code and must honor the passed abort signal and validate their own operational limits.

Tools marked `requiresApproval` execute only after an explicit positive callback. Interactive CLI asks the user to type `yes`; one-shot CLI and Telegram deny approval-required tools. The approval flag is a contract, **not an OS sandbox**. Future shell/browser tools must add isolation before being enabled. Failed turns cannot roll back external actions, so future state-changing tools also need durable action journaling and idempotency.

`.env`, SQLite databases, dependencies, and build output are ignored by Git. Do not place credentials in prompts or commit them. CLI access trusts the local OS user; `CLI_USER_ID` is a namespace, not remote authentication.

## Development and checks

```sh
pnpm build       # Compile all workspace packages
pnpm typecheck   # Check application and test types
pnpm test        # Build and run offline tests
pnpm check       # Typecheck and tests (also used by CI)
pnpm cli --help
```

Tests cover SQLite reopen/isolation, history retention, reset, tool execution/approval/validation, iteration limits, concurrent turns, provider wire formats and error handling, gateway authorization, Telegram filtering/offsets/retries/chunking, and an actual compiled CLI process against a local HTTP Ollama fixture. No AWS account, Telegram token, running Ollama, or paid calls are needed for tests. Live provider/account access must be checked separately when configuring your environment.

See [SPEC.md](SPEC.md) for the project scope and [PROGRESS.md](PROGRESS.md) for delivery status and next work.
