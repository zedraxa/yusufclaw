# YusufClaw Gateway Subsystem — Python Port (py-gateway)

## 1. Executive Summary

The **Gateway subsystem** (`src/yusufclaw/gateway`) has been ported from TypeScript (`ts-final`) to Python 3.12 (`uv`), conforming to the specifications in `packages/gateway/src/{bus,cli,config,index,main,telegram,web,web-page}.ts`, `docs/ARCHITECTURE-v2.md`, and relevant test suites.

All core gateway channels (CLI, Web, and Telegram), the asynchronous event bus, the configuration validation schema, and the human-in-the-loop approval protocol have been implemented and verified with 100% offline tests, strict MyPy type checking, and Ruff linting.

```
+---------------------------------------------------------------------------------------+
|                                    YusufClaw Gateway                                  |
+------------------------+-------------------------------+------------------------------+
|       CLI Gateway      |          Web Gateway          |       Telegram Gateway       |
|  - Interactive prompt  |  - Minimal Chat Web Page      |  - Long polling & webhooks   |
|  - One-shot turns (-m) |  - Rest API (/api/chat)       |  - Allowlist authorization   |
|  - Approval prompts    |  - AG-UI 1.0 SSE stream       |  - Unicode chunking (<=4000) |
|                        |  - Scheduled inbox delivery   |  - Inline approval cards     |
+------------------------+-------------------------------+------------------------------+
                                         |
                                         v
                         +-------------------------------+
                         |          MessageBus           |
                         |  - Per-channel allowlists     |
                         |  - Isolated JSON session keys |
                         +-------------------------------+
                                         |
                                         v
                         +-------------------------------+
                         |          Core Agent           |
                         |  (LangGraph + Memory + Tools) |
                         +-------------------------------+
                                         |
                       (ActionCard interactive approvals)
                                         v
                         +-------------------------------+
                         |    ApprovalProvider (YC-05)   |
                         |  - Nonce verification         |
                         |  - Replay protection          |
                         |  - Argument tampering checks  |
                         +-------------------------------+
```

---

## 2. Ported Components

### 2.1 Async Event Bus (`src/yusufclaw/gateway/bus.py`)
- **`IncomingMessage`**: Strongly-typed message payload supporting both `camelCase` (`userId`, `conversationId`, `requestId`) and `snake_case` attributes.
- **`MessageBus`**:
  - Enforces per-channel allowlists (`cli`, `telegram`, `web`).
  - Raises `UnauthorizedUserError` (inherits from `PermissionError`) when an unlisted sender attempts to dispatch a message.
  - Constructs deterministic isolated session keys: `json.dumps([channel, userId, conversationId], separators=(",", ":"))`.
  - Dispatches message turns directly into `Agent.handle(session, requestId, text, approve=approve)`.

### 2.2 ApprovalProvider Protocol & ActionCard Formatting (`src/yusufclaw/gateway/approval.py`)
- Defines the decoupled contract for human-in-the-loop tool execution approval (YC-05).
- **`ActionCard` & `ActionCardRendered`**: Pydantic data structures representing immutable, unalterable human-facing action cards with title, exact command, effects, taint status, and reversibility.
- **`ApprovalProvider` Protocol**:
  - Defined as a `typing.Protocol` with `@runtime_checkable`.
  - **No concrete implementation is coupled to the gateway.** The concrete implementation is owned by the worker handling `src/yusufclaw/security` and `src/yusufclaw/tools`.
- **`format_telegram_card_message`**: Deterministically formats an `ActionCard` into a localized Turkish alert message (`⚠️ [ONAY GEREKİYOR: ...]`) and cleanly segregates untrusted model claims (`[Ajanın İddiası (Güvenilmeyen Açıklama)]`).
- **`build_telegram_card_keyboard`**: Produces Telegram inline keyboard markup with verifiable callback query data (`approve:<id>:<nonce>`, `deny:<id>:<nonce>`, `details:<id>:<nonce>`).

### 2.3 Telegram Gateway (`src/yusufclaw/gateway/telegram.py`)
- **`TelegramApi`**: Production HTTP client communicating with `https://api.telegram.org/bot{token}/{method}`.
  - Masks token-bearing URLs in exceptions to prevent credential leakage.
  - Respects HTTP 429 rate limits via `retry_after`.
- **`split_telegram_text`**: Splits responses exceeding Telegram's 4096-character limit into chunks of at most 4000 characters without breaking multi-byte Unicode or emoji codepoints.
- **`TelegramAdapter`**:
  - Validates non-empty user allowlists (`TELEGRAM_ALLOWED_USER_IDS`).
  - Ignores group chats, bot messages, non-text updates, and unauthorized users.
  - Offsets are persisted to memory state under `telegram-offset:<sha256(token)>` and resumed upon restart.
  - Retries transient polling failures with exponential/retry-after backoff.
  - Fails fast upon terminal polling conflicts (HTTP 401, 403, 409).
  - Sanitizes internal exceptions and error text from replies.
  - Connects inline keyboard approval callbacks through `ApprovalProvider`.
  - Supports webhook update processing via `handle_webhook()`.

### 2.4 FastAPI Web Gateway & AG-UI SSE Streaming (`src/yusufclaw/gateway/web.py`, `web_page.py`)
- **Static Assets & Web Page**:
  - Serves token-free HTML (`GET /`), JavaScript (`GET /app.js`), and CSS (`GET /style.css`).
  - Enforces strict Content Security Policy (`default-src 'none'; script-src 'self'; ...; frame-ancestors 'none'`).
  - JavaScript contains `textContent` DOM manipulation without `innerHTML`, `localStorage`, or `sessionStorage`.
- **Security & Authorization**:
  - Constant-time Bearer token comparison (`WEB_AUTH_TOKEN`) using SHA-256 digests.
  - Origin verification: Denies cross-origin requests (`Origin` header mismatch with host returns 403).
  - Request constraints: Strict `application/json` content type enforcement (415) and body size limit of 100,000 bytes (413).
  - Input validation: Strict schema with `extra="forbid"`, rejecting unknown attributes or empty/oversized messages (400).
- **Endpoints**:
  - `GET /health`: Health probe returning status, version, service name, and model provider.
  - `GET /api/routines`: Authenticated inbox returning scheduled routine replies for the user.
  - `POST /api/chat`: Primary chat endpoint returning `{"reply": reply}`, masking internal exceptions to 502.
  - `POST /api/chat/stream`: Dedicated Server-Sent Events stream for conversational turns.
  - `GET /api/events`: Persistent SSE stream for system connection and progress notifications.
- **AG-UI 1.0 Event Stream**:
  - Emits formatted SSE events:
    - `progress`: `{"status": "thinking", "step": 1, "requestId": ...}`
    - `message`: `{"role": "assistant", "reply": ..., "requestId": ...}`
    - `done`: `{"status": "completed", "requestId": ...}`
    - `error`: Sanitized error payload.
- **`WebAdapter`**:
  - Manages background Uvicorn server task and socket binding (supports port 0 for ephemeral testing).
  - Handles scheduled inbox delivery (`deliver(target, reply, requestId)`).
  - Cleanly closes connections and server tasks.

### 2.5 CLI Gateway (`src/yusufclaw/gateway/cli.py`)
- **`run_cli`**: Conforms to the TypeScript CLI runner interface:
  - Supports one-shot execution via `-m` / `--message` and `-s` / `--session`.
  - Non-interactive invocation without `--message` fails fast with `RuntimeError("Use --message for non-interactive input")`.
  - Interactive mode provides a persistent terminal loop with `You> `, commands `/help` and `/exit`, and interactive tool approval prompts (`Approve <tool> <args>? Type yes: `).
  - Sanitizes unexpected errors and outputs clean messages to stderr.
- **Typer CLI (`cli_app`)**:
  - Registered as `yusufclaw` in `[project.scripts]`.
  - Supports `turn`, `chat`, `version`, and root execution with `--message`.

### 2.6 Configuration Loader (`src/yusufclaw/gateway/config.py`)
- Strongly typed `GatewayConfig` with Pydantic validation:
  - Requires `BEDROCK_MODEL_ID` when provider is `bedrock`.
  - Requires `OPENAI_API_KEY` and `OPENAI_MODEL` when provider is `openai`.
  - Validates numeric user IDs in `TELEGRAM_ALLOWED_USER_IDS`.
  - Validates Telegram bot token format (`^\d+:[A-Za-z0-9_-]+$`).
  - Validates positive integers for `MAX_AGENT_STEPS`, `MAX_TOKENS`, and `MODEL_TIMEOUT_MS`.
  - Validates HTTP/HTTPS scheme for `OLLAMA_BASE_URL`.
- Provides `load_config(env)` and alias `loadConfig(env)` matching TS.

### 2.7 Main Entrypoint (`src/yusufclaw/gateway/main.py`)
- Orchestrates configuration, SQLite memory, model provider, Agent, and MessageBus.
- Dispatches execution to `cli`, `web`, or `telegram` modes.
- Listens for `SIGINT` and `SIGTERM` signals for graceful shutdown of web adapters and database connections.

---

## 3. The ApprovalProvider Contract

The `ApprovalProvider` protocol lives in `src/yusufclaw/gateway/approval.py` and is defined as:

```python
from typing import Any, Protocol, runtime_checkable
from yusufclaw.gateway.approval import ActionCard, ApprovalRecord, ApprovalResult

@runtime_checkable
class ApprovalProvider(Protocol):
    """Protocol for human approval verification and action card lifecycle (YC-05)."""

    def register(self, card: ActionCard, input_args: dict[str, Any] | None = None) -> None:
        """Register an ActionCard with its expected arguments."""
        ...

    def consume(
        self,
        approval_id: str,
        nonce: str,
        actual_args: dict[str, Any] | None = None,
    ) -> ApprovalResult:
        """Consume an approved action card with replay and argument tampering protection."""
        ...

    def deny(self, approval_id: str, nonce: str) -> None:
        """Deny approval for the specified card and nonce."""
        ...

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieve stored approval record by ID."""
        ...
```

### Guarantees and Expectations for Security/Tools Subsystem:
1. **Replay Protection**: When `consume()` is called more than once with the same `approval_id` and `nonce`, it **must** return `ApprovalResult(success=False, reason="Replay detected")`.
2. **Argument Tampering Protection**: If `actual_args` passed to `consume()` does not match the SHA-256 hash or registered arguments of the card, it **must** return `ApprovalResult(success=False, reason="Argument tampering detected")`.
3. **Nonce Validation**: If an invalid `nonce` is provided, consumption **must** fail.
4. **Denial Lifecycle**: Once `deny()` is called for an approval ID, subsequent `consume()` attempts **must** be rejected.
5. **Decoupling**: The gateway subsystem depends **only** on this protocol. It never constructs concrete security policies or taint graphs.

---

## 4. Verification and Test Suites

Every TypeScript test case from `ts-final` in `tests/{gateway,web,cli}.test.ts` was ported to `tests/python/`:

| Test Suite | TS Origin | Status | Test Cases |
|------------|-----------|--------|------------|
| `tests/python/test_bus.py` | `tests/gateway.test.ts` (bus section) | **Passed** | Allowlist authorization, channel isolation, session formatting |
| `tests/python/test_gateway_config.py` | `tests/gateway.test.ts` (config section) | **Passed** | Bedrock defaults, OpenAI mode validation, Ollama scheme, regex token validation |
| `tests/python/test_telegram_gateway.py` | `tests/gateway.test.ts` (telegram & YC-05) | **Passed** | Unlisted users, bot/group ignore, offset persistence, 409 conflict, retry-after backoff, emoji chunking, token masking, ActionCard formatting, inline approval callback queries, replay rejection, argument tampering rejection |
| `tests/python/test_web_gateway.py` | `tests/web.test.ts` + AG-UI spec | **Passed** | Bearer auth, cross-origin blocking, payload constraints, token-free HTML/JS, CSP headers, shared history, error sanitization, routine delivery, ephemeral socket lifecycle, AG-UI SSE streaming |
| `tests/python/test_cli_gateway.py` | `tests/cli.test.ts` | **Passed** | One-shot mode (`-m`, `-s`), non-interactive TTY check, interactive prompt, approval callbacks, error sanitization, Typer subcommands |

### Quality & Tooling Metrics:
- **`uv run pytest`**: **60 passed** in 5.36s (100% offline, zero network access, zero external services).
- **`uv run ruff check .`**: **All checks passed** (0 warnings, 0 errors).
- **`uv run mypy src tests/python`**: **Success: no issues found in 43 source files** (strict mode enabled).

---

## 5. Identified Gaps & Future Handover Notes

1. **Scheduled Routines Runner (`routines.ts`)**:
   - As instructed, `routines.ts` was deferred to the skills phase. The web gateway implements the routine delivery inbox (`WebAdapter.deliver`, `/api/routines`), ready to receive scheduled executions once the cron scheduler is ported.
2. **Concrete ActionCardStore Implementation**:
   - Owned by the worker porting `src/yusufclaw/security` and `src/yusufclaw/tools`. When complete, pass their store instance directly into `TelegramAdapter(..., approval_provider=card_store)`.
3. **Production Webhook Route for Telegram**:
   - `TelegramAdapter.handle_webhook()` is implemented and ready. When deploying in webhook mode rather than polling, wire a FastAPI route (e.g. `POST /webhook/telegram/{token}`) calling `adapter.handle_webhook(update)`.
