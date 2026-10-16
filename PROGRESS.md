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

## Phase 1 follow-up plan (historical)

The spec names only Phase 1's delivery boundary. Suggested next work for its broader roadmap:

1. Add sandboxed shell and scoped file tools with durable action records, idempotency, and channel-specific approvals before enabling destructive operations.
2. Add web fetching, Playwright browser tools, and local `agy`/Codex CLI delegation with resource and network limits.
3. Implement trusted skill loading and scheduled routines; add an OpenAI-compatible provider and web chat.
4. Improve long-term memory with token-aware context selection/summarization, retention policies, and migration/version support.
5. Add a durable Telegram outbox and cross-process session coordination. Current delivery is at least once; send/offset crashes or partial chunk failures can duplicate replies.
6. Design and validate CDK infrastructure, DynamoDB memory, S3 files, and webhook/heavy-job hosting before a separately authorized AWS deployment. WhatsApp and email remain later channels.

Phase 1 operational limits (historical): memory is plaintext on the local host, history selection is turn-count based, skill files are not executed, and there are no shell/file/browser/delegation tools or cloud resources. The approval interface is not a sandbox. Run one writer per conversation; Phase 1's session queue does not coordinate separate processes.


# Phase 2 tools progress

Phase 2 tools specification from SPEC.md is complete on branch `phase2-tools`.

## Built

- **Sandboxed shell tool (`shell`)**:
  - Enforces executable allowlist (`DEFAULT_ALLOWED_COMMANDS` including standard development and inspection utilities).
  - Dynamic dangerous/destructive command classification (`isDangerousCommand`): safely permits read-only commands (`ls`, `cat`, `head`, `pwd`, `git status`, `git diff`, `node -v`, etc.) while requiring explicit approval for destructive operations (`rm`, `mv`, `mkdir`, `kill`, `chmod`, `git commit`, `git reset`, `pnpm install`, file redirections `>`, etc.).
  - Enforces workspace directory confinement for execution `cwd` (rejects path escape attempts).
  - Enforces timeouts (default 30s, max 60s) with clean subprocess group termination, abort signal integration, and output limits (truncated at 32,000 characters).
- **Scoped file tools (`file_read`, `file_write`, `file_list`)**:
  - Strict workspace path confinement (`resolveSafePath`): prevents path traversal (`..`), absolute path escapes outside workspace root, null-byte injections, and symlink escapes (for both existing files and parent directory traversal).
  - `file_read`: bounded reads (default 64KB, max 1MB) with truncation markers; read-only (`requiresApproval: false`).
  - `file_write`: writes or creates parent directories recursively; requires approval (`requiresApproval: true`).
  - `file_list`: directory listing with file types (`[FILE]`, `[DIR]`), entry sizes, optional recursive traversal, and max entries bounding (default 200, max 1000); read-only (`requiresApproval: false`).
- **Web fetch tool (`web_fetch`)**:
  - HTTP and HTTPS protocol validation (blocks non-web protocols like `file:` or `ftp:`).
  - SSRF protection: blocks cloud instance metadata services (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`, `100.100.100.200`).
  - Streaming size bounding via response reader (default 100KB, max 1MB) preventing memory blowup from huge payloads.
  - Timeout enforcement with combined abort signals and custom headers support.
  - Injectable `fetcher` interface for offline testing.
- **Browser tool (`browser`)**:
  - Playwright browser integration for headless web browsing (actions: `navigate`, `content`, `title`).
  - Pure optional dependency handling: dynamically imports Playwright at runtime, returning a clear, graceful instructional message if not installed (`Playwright is not installed. To use the browser tool...`).
  - Complete resource lifecycle management: guarantees closing pages, contexts, and browser instances in `finally` blocks.
  - Pluggable launcher interface for offline unit testing.
- **Delegate-to-local-agents tool (`delegate_agent`)**:
  - Subprocess execution delegating to local CLI agents: `agy -p <prompt>` or `codex exec <prompt>`.
  - Environment gate: disabled by default unless explicitly enabled via `ENABLE_AGENT_DELEGATION=true` or options.
  - Requires approval (`requiresApproval: true`), enforces workspace directory confinement, execution timeouts, and graceful missing-binary handling.
  - Pluggable runner interface for offline testing.
- **Agent core loop & approval integration**:
  - Registered all Phase 2 tools into `createDefaultTools()` and wired to `Agent` in `main.ts`, preserving `current_time`.
  - Upgraded `AgentTool.requiresApproval` to accept `boolean | ((input: Record<string, unknown>) => boolean)` so dangerous/destructive commands trigger the approval callback while safe inspection commands run automatically.
  - Interactive CLI prompts user approval (`Type yes:`), while one-shot CLI and Telegram deny approval-required actions safely.

## Verification

Verified with Node v20.20.2 and pnpm 9.15.9:
- `pnpm typecheck` passed cleanly across all monorepo packages and test projects.
- `pnpm test` passed: all **44 tests in 5 files** passed.
- Unit and integration tests cover:
  - Command allowlists, safe vs. dangerous command detection, cwd confinement, timeout and truncation in `shell`.
  - Path traversal, absolute path escape, symlink escape, null bytes, read/write/list in `files`.
  - Protocol validation, SSRF metadata blocking, stream chunk size truncation in `web_fetch`.
  - Graceful missing-dependency fallback, page navigation, and context cleanup in `browser`.
  - Environment activation check, CLI command delegation (`agy`, `codex`), and error handling in `delegate_agent`.
  - Approval flow integration in the agent loop for both approved and unapproved calls.
  - Existing CLI integration, memory isolation, Telegram polling, and Bedrock/Ollama provider tests remain 100% passing.

## Phase 2 models/web/skills

Implemented on `phase2-models-web`:

- OpenAI-compatible nonstreaming Chat Completions provider in `packages/models`:
  env-selected base URL, API key and model, function tool schemas/call IDs/results,
  history translation, output limit, timeout/cancellation, response validation,
  redirect rejection and sanitized failures.
- Small web HTTP adapter and static chat page in `packages/gateway`. Defaults to
  `127.0.0.1:3000`; requires `WEB_AUTH_TOKEN`. Chat and scheduled replies require
  bearer authentication and use the existing authorized message bus/core. Includes
  input/body limits, cross-origin API rejection, plain-text rendering, named
  sessions, replay/reset and graceful cancellation. Tokens are not persisted in
  browser storage or URLs. `pnpm web` starts it.
- Trusted Markdown skill loading in `packages/core`: deterministic recursive
  loading from `skills/` (configurable), system-prompt injection, size/depth bounds,
  and exclusion of README, hidden entries, symlinks and executable files. No code
  execution, include expansion, tool installation or remote fetching.
- Configured routines in `packages/core`: five-field numeric UTC cron with lists,
  ranges and steps, validated JSON files, serialized execution, stable occurrence
  IDs and SQLite completion markers. Dispatch goes through the shared bus;
  gateway delivery supports CLI/stdout, allowlisted Telegram private chats and an
  authenticated web inbox. `pnpm routines` runs a dedicated scheduler; web and
  Telegram modes also start it when configured. Ordinary CLI does not.
- README, skill-folder guidance and `.env.example` updated with configuration,
  commands, routine examples and operational limits. No new dependencies.

Verification used the existing isolated Node **v20.20.2** / pnpm **9.15.9** toolchain
from the npm cache, without changing the system Node installation:

- `pnpm install --frozen-lockfile` passed.
- `pnpm check` passed: application/test TypeScript checks and **53 tests in 8 files**.
- Local HTTP OpenAI fixtures verify real tool round trips, auth/base path, history,
  omitted empty tools, malformed JSON/arguments, incomplete responses, HTTP errors,
  timeout and cancellation. Existing Phase 1 tests continue to pass.
- Web HTTP tests cover token enforcement, origin/input/body checks, history/session
  isolation, replay/reset, sanitized errors, scheduled inbox delivery and shutdown.
- Core tests cover skill filtering/injection/limits, config validation, cron
  semantics, delivery retries with model reply reuse, persisted deduplication,
  overlapping ticks, target authorization and cancellation.
- A compiled-entry-point integration test loads skills and a routine file, calls
  the local OpenAI fixture, delivers to CLI/stdout, exits cleanly on SIGTERM, and
  verifies web startup rejects a missing auth token.

Operational limits: use one scheduler process per config/database. Missed minutes
are skipped; failed deliveries retry only while their scheduled minute is current.
A send/completion-marker crash can duplicate Telegram/stdout delivery; there is no
transactional outbox or cross-process lock. The web inbox retains the last 100
replies and deduplicates occurrence IDs. Web is a single trusted-user UI, with all
token holders sharing the configured identity. Skills and routines reload only on
restart. Live external provider and Telegram access were not exercised.

`packages/tools` and `packages/infra` were not modified. No AWS deployment, real
credentials, or pushes were performed.

# Main integration — 2026-10-16

- Merged `phase2-tools`, then `phase2-models-web`; gateway startup now combines
  the complete default tool registry with skills, OpenAI, authenticated web and routines.
- Imported tracked CDK project files from `yusufclaw-infra` commit
  `613c5a1e571cfc96925783917a007e569f06a1e9` into `packages/infra` by copy.
  Dependencies, CDK output and build artifacts were excluded. The existing
  `packages/*` workspace glob includes infra; dependencies use the root lockfile.
- Preserved the infra CommonJS build and Jest tests, enabled TypeScript project
  references, and included Jest in root `pnpm check`. Vitest is scoped to application tests.
- Extended the compiled OpenAI/skills/routines test to check full tool registration.
- Known limits: shell checks are not OS isolation; browser/delegation require
  optional local dependencies. Infra still needs webhook-to-agent integration,
  compiled Lambda deployment packaging and an application container image.
  Budget notifications do not enforce a hard spending cap. Existing scheduler
  delivery/cross-process limits remain as documented above.
- No AWS-mutating commands or deployment were run during integration.
- Verification: isolated Node v20.20.2 / pnpm 9.15.9; `pnpm install`,
  `pnpm check`, and standalone infra build passed. **68 Vitest tests in 9 files
  plus 6 Jest tests in 1 suite (74 total)** passed. CDK emitted a Lambda Node 20
  runtime deprecation warning; review the runtime before deployment.
- A credential-pattern scan found no matches in tracked/imported files.
