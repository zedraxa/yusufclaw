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
