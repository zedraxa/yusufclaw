# YusufClaw AG-UI 1.0 Web Gateway — Architecture & Implementation Guide (py-agui)

## 1. Executive Summary

This document describes the design and implementation of the **AG-UI 1.0-compatible Server-Sent Events (SSE) event stream**, **human-in-the-loop approval workflow (YC-05)**, and **real-time Web Chat UI** for the YusufClaw Web Gateway on the `py-agui` branch.

Conforming to `docs/ARCHITECTURE-v2.md` (Interfaces layer: web AG-UI 1.0), `PY-GATEWAY.md`, and the strict operational security requirements of YusufClaw, this implementation upgrades the previous static stub stream (`progress -> message -> done`) into a fully interactive, incremental, and observable runtime event stream.

```
+-----------------------------------------------------------------------------------------------+
|                                  Browser Client (Web UI)                                      |
|  - SSE EventSource / fetch stream reader                                                      |
|  - Strict CSP: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'   |
|  - Safe DOM: textContent only (no innerHTML, no eval, no localStorage/sessionStorage)         |
+-----------------------------------------------------------------------------------------------+
        |                                                   ^
        | POST /api/chat/stream                             | SSE Stream (text/event-stream)
        | POST /api/chat/approval                           |
        v                                                   |
+-----------------------------------------------------------------------------------------------+
|                                    YusufClaw Web Gateway                                      |
|  - Bearer Token Auth (Authorization: Bearer <token>)                                          |
|  - Cross-Origin Validation (Origin/Sec-Fetch-Site check)                                       |
|  - Strict Body Size Limits (max 100KB)                                                        |
+-----------------------------------------------------------------------------------------------+
        |                                                   ^
        | bus.dispatch(...)                                 | TurnEventHook / asyncio.Queue
        v                                                   |
+-----------------------------------------------------------------------------------------------+
|                                          Core Agent                                           |
|  - model_node: token streaming deltas (TextMessageContent) or non-streaming fallback         |
|  - tool_node: tool execution lifecycle (ToolCallStart, ToolCallArgs, ToolCallEnd)            |
|  - approval_node: human-in-the-loop ActionCard emission & waiting                             |
+-----------------------------------------------------------------------------------------------+
        |                                                   ^
        | verify_and_consume(...)                           | ActionCard approval/denial
        v                                                   |
+-----------------------------------------------------------------------------------------------+
|                                InMemoryApprovalProvider (YC-05)                               |
|  - Nonce verification & single-use replay protection                                          |
|  - Canonical argument SHA-256 tampering check                                                 |
|  - Denial state recording & audit logging                                                     |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. AG-UI 1.0 SSE Protocol Specification

The web gateway endpoint `POST /api/chat/stream` returns a standard `text/event-stream` HTTP response. Each event follows the standard SSE wire format:

```http
event: <EventType>
data: <JSON-encoded payload>

```

### 2.1 Complete Event Types & Wire Payloads

| Event Name | Purpose | Key Attributes | Backward Compatibility |
| :--- | :--- | :--- | :--- |
| `RunStarted` | Emitted when agent execution begins | `requestId`, `conversationId`, `channel`, `timestamp` | AG-UI 1.0 lifecycle |
| `progress` | Stage / status indication | `stage`, `message` | Legacy UI compatibility |
| `ToolCallStart` | Notification that a tool is being invoked | `toolCallId`, `toolName` | AG-UI 1.0 tool observability |
| `ToolCallArgs` | Sanitized arguments passed to tool | `toolCallId`, `toolName`, `args` (sanitized) | AG-UI 1.0 tool observability |
| `ToolCallEnd` | Tool execution result or error | `toolCallId`, `toolName`, `status` (`success`/`failed`), `durationMs` | AG-UI 1.0 tool observability |
| `ActionCard` | Interactive approval card requiring human consent | `approvalId`, `nonce`, `title`, `command`, `effects`, `taint`, `reversible`, `expiresAt` | YC-05 approval workflow |
| `approval` | Alias for `ActionCard` for dual compatibility | Same as `ActionCard` | Legacy approval listeners |
| `TextMessageStart` | Beginning of streaming text generation | `messageId`, `role` (`assistant`) | AG-UI 1.0 token streaming |
| `TextMessageContent`| Mid-turn incremental text delta | `messageId`, `delta` | AG-UI 1.0 token streaming |
| `TextMessageEnd` | Completion of streaming text generation | `messageId`, `totalLength` | AG-UI 1.0 token streaming |
| `message` | Complete accumulated assistant response | `reply` | Legacy client compatibility |
| `done` | Turn completion indicator | `conversationId`, `status` (`completed`) | Legacy client compatibility |
| `RunFinished` | Lifecycle termination event | `requestId`, `conversationId`, `status`, `timestamp` | AG-UI 1.0 lifecycle |
| `error` | Turn-level or runtime error | `code`, `message`, `detail` | Error handling |

### 2.2 Dual Streaming & Non-Streaming Fallback

- **Incremental Streaming**: When the underlying model provider or agent loop emits mid-turn text chunks, the gateway forwards each delta as a `TextMessageContent` event in real time.
- **Non-Streaming Fallback**: If the model provider produces a single complete reply without chunking, the gateway emits a coherent sequence (`TextMessageStart` -> `TextMessageContent` [entire text] -> `TextMessageEnd`), followed by the standard `message` event. The client receives a uniform protocol regardless of model backend.

---

## 3. Secret Sanitization & Data Masking Engine

To prevent credential leaks over SSE streams, logs, and browser DOM:

1. **`sanitize_secret_patterns(text: str) -> str`**:
   - Redacts HTTP Authorization Bearer tokens: `Bearer [REDACTED]`
   - Redacts AWS access key IDs: `AKIA...` -> `[REDACTED_AWS_KEY]`
   - Redacts API tokens: `ghp_...`, `gho_...`, `sk-...`, `sk-proj-...`, `glpat-...` -> `[REDACTED_TOKEN]`
   - Redacts basic auth credentials inside URLs: `http://user:pass@host/` -> `http://***:***@host/`
2. **`sanitize_args_summary(args: Any) -> Any`**:
   - Recursively traverses dictionaries and lists.
   - Keys matching sensitive substrings (`password`, `secret`, `token`, `key`, `auth`, `credential`, `api_key`, `private_key`, `bearer`, `webhook`) have their values replaced with `[REDACTED]`.
   - String values are scrubbed with `sanitize_secret_patterns` and truncated to 300 characters to prevent prompt injection and payload blowup.
3. **`sanitize_result_summary(result: Any) -> Any`**:
   - Scrubbed and truncated to at most 500 characters.

---

## 4. Human-In-The-Loop Approval Subsystem (YC-05)

### 4.1 In-Memory Approval Provider (`src/yusufclaw/gateway/approval.py`)

The `InMemoryApprovalProvider` implements the `@runtime_checkable class ApprovalProvider(Protocol)` without requiring external cloud databases:

- **Single-Use Nonce**: Each `ActionCard` is minted with a cryptographically secure random UUID nonce (`secrets.token_hex(16)`).
- **Anti-Replay**: Once resolved (approved or denied), the card cannot be reused. Subsequent approval attempts fail with HTTP 400 (`Replay detected`).
- **Tampering Detection**: When creating an action card, a canonical SHA-256 hash of the execution arguments is computed. Submissions with altered arguments are rejected with HTTP 400 (`Action arguments have been tampered with`).
- **Denial Recording**: Denials are persisted in the provider state and audit logged; re-approving a denied action is forbidden.
- **Expiration**: Expired cards are immediately invalidated.

### 4.2 Web Approval Endpoints

Two alias endpoints are exposed:
- `POST /api/chat/approval`
- `POST /api/approval`

Both endpoints require:
1. `Authorization: Bearer <valid_token>` header.
2. Valid `Origin` or `Sec-Fetch-Site` header (rejects unauthorized cross-origin requests).
3. Payload adhering to `ApprovalInput` (`extra="forbid"`):
   ```json
   {
     "approvalId": "app-uuid",
     "nonce": "nonce-hex",
     "action": "approve"  // or "deny", supports "decision" alias
   }
   ```
4. Returns HTTP 200 `{"status": "approved", "approval_id": "app-uuid"}` or HTTP 400 with a sanitized error message on failure.

---

## 5. Agent Turn Event Hook Mechanism (`src/yusufclaw/agent.py`)

To decouple the core agent engine from web-specific event streaming, a non-intrusive `TurnEventHook` was introduced:

- **ContextVar-backed**: Uses Python's `contextvars.ContextVar` to allow per-request, concurrent event subscribers without mutating global state.
- **Optional Parameter**: `Agent.__init__(turn_callback=...)` and `Agent.handle(..., on_event=...)` accept optional callbacks.
- **Default Behavior Unchanged**: When no hook is registered, the agent executes normally with zero overhead.
- **Emitted Hook Events**:
  - `model_node`: emits `text_delta` during model token generation.
  - `tool_node`: emits `tool_call_start`, `approval_requested` (if approval is pending), and `tool_call_end`.

---

## 6. Minimal Web Chat UI (`src/yusufclaw/gateway/web_page.py`)

The browser interface served by `GET /` and `/chat.js` was upgraded to support full AG-UI 1.0 real-time rendering:

### 6.1 DOM Layout & Styling
- `#chat-history`: Conversational turns container.
- `#tools`: Dynamic container displaying active tool execution chips and results.
- `#approvals`: Human-in-the-loop action card container displaying:
  - Action card title and command.
  - Effects and taint warnings.
  - Interactive "Approve" (green) and "Deny" (red) buttons.
- `#progress`: Real-time stage updates.

### 6.2 Browser Security & CSP Conformance
- **Strict CSP**: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'`.
- **Zero XSS Vectors**: All dynamic text is injected via `element.textContent = ...` or `document.createTextNode(...)`. The prohibited `innerHTML` property is never used.
- **Zero Insecure Storage**: Neither `localStorage` nor `sessionStorage` is used. Session tokens are kept exclusively in memory or passed via explicit headers.
- **Self-Contained Client**: Served as static assets (`/` and `/chat.js`) without external CDN dependencies.

---

## 7. Verification & Test Suite

All functionality is tested 100% offline with zero cloud credentials or live network connections:

| Test Name | Coverage |
| :--- | :--- |
| `test_web_auth_cross_origin_validation_and_token_free_page` | Token auth, origin validation, token-free HTML |
| `test_web_chat_success_and_error_sanitization` | Chat dispatch and error sanitization |
| `test_web_scheduled_replies_delivery_and_inbox` | Scheduled routine inbox delivery |
| `test_web_adapter_requires_token_and_socket_lifecycle` | Adapter token validation and server binding |
| `test_web_ag_ui_sse_streaming` | Legacy stream format compatibility |
| `test_web_ag_ui_1_0_run_lifecycle_and_fallback` | Full `RunStarted`/`RunFinished` lifecycle and fallback deltas |
| `test_web_ag_ui_1_0_mid_turn_incremental_streaming` | Real-time `TextMessageContent` token chunks |
| `test_web_ag_ui_1_0_tool_calls_and_secret_masking` | `ToolCallStart/Args/End` and secret token masking |
| `test_web_ag_ui_1_0_interactive_approval_lifecycle_and_yc05_checks` | YC-05 approval, replay rejection, tamper rejection |
| `test_web_ag_ui_1_0_denial_lifecycle` | Card denial recording and re-approval prevention |
| `test_web_ui_rendered_elements_and_approval_controls` | HTML/JS UI element inspection, CSP safety, no storage |
| `test_sanitize_secret_patterns_and_args_summary` | Regex masking of Bearer, AWS, generic tokens, URL credentials |
| `test_approval_input_validation` | Pydantic validation, aliases, and extra-field rejection |

**Results**:
- `uv run pytest`: 147 passed, 1 skipped.
- `uv run ruff check .`: 0 errors, 0 warnings (`All checks passed!`).
- `uv run mypy src`: 0 errors across 68 source files.
