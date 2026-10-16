# Python Web Gateway AG-UI 1.0 & UX Sprint 1 (UX-01..UX-06)

This document specifies the architecture, data models, protocols, and implementation details for Sprint 1 (backlog items UX-01 through UX-06) of the YusufClaw Agentic Web Gateway, conforming to `docs/UX-SPEC.md` and `docs/ARCHITECTURE-v2.md`.

---

## 1. Executive Summary & Goals

The primary goal of UX Sprint 1 is to elevate YusufClaw's web interface into a responsive, verifiable, multi-agent cognitive workspace speaking the **AG-UI 1.0** protocol. The implementation delivers:

1. **UX-01 (AG-UI 1.0 Protocol Conformance Gaps):** Standardized, strongly-typed JSON event stream envelopes (`run.acknowledged`, `run.milestone`, `message.delta`, `message.thought`, `run.completed`) alongside full backward compatibility for legacy web clients.
2. **UX-02 (Named Agents Sidebar & Session Router):** Multi-agent routing with composite session keys (`web:{userId}:{agentId}:{conversationId}`) ensuring conversational memory isolation between specialized personas (`orchestrator`, `coder`, `researcher`, `ops`).
3. **UX-03 (Immediate Cognitive Ack & Milestone Stepper):** Immediate perceptual feedback (<300ms) with an ack banner and live milestone step indicators during long-running tool execution loops.
4. **UX-04 (Inline Decision/Question Widgets):** Interactive question options and chip pickers (`widget.question`) with callback resolution via `POST /api/actions/resolve-widget` and replay prevention.
5. **UX-05 (Verifiable Action Cards & Cross-Channel Parity):** Human-in-the-loop action approval cards (`widget.approval_card`) featuring cryptographic nonces, TTL countdowns (300s), taint badges, argument anti-tampering protection, and live bidirectional synchronization with Telegram/CLI approval providers.
6. **UX-06 (Masked Secret-Request Modal & Local Credential Broker):** Zero-leak credential acquisition modal (`widget.secret_request`) backed by an isolated `CredentialBroker`. Secrets are stored exclusively in memory/local keyring, SHA-256 fingerprinted, and injected as innocuous system pills—guaranteeing plaintext credentials never enter model prompts, SQLite `turns`, or logs.

---

## 2. Research Grounding & Academic Citations

The implementation directly operationalizes foundational findings from `docs/UX-SPEC.md` and related literature:

- **Perceptual Latency & Cognitive Ack (<300ms):** Following Nielsen Norman Group (*Miller, 1968; Card et al., 1983*), operations taking longer than 300ms must provide immediate feedback. Emitting `run.acknowledged` and the initial `run.milestone` within milliseconds guarantees the user perceives instantaneous system engagement.
- **Simon Willison's Lethal Trifecta Mitigation:** To prevent Prompt Injection + Tool Access + Exfiltration, credentials are sequestered in `CredentialBroker` outside model context, turning raw secret strings into opaque fingerprints (`[Key configured: <name> (sha256: <fp>)]`).
- **FIDES Information Flow Control (IFC):** Action cards strictly track `taint` status (`clean` vs `contaminated`) and reversible guarantees (`reversible: bool`), preventing unauthorized execution of untrusted tool calls.
- **Cross-Channel Parity & Nonce Verification:** Action cards are bound to cryptographically secure nonces and verified arguments. Consuming an approval card on Telegram instantly resolves the active web SSE stream via an asynchronous watcher without client polling.

---

## 3. Architecture & Data Models

### 3.1 AG-UI Envelope Schema (`src/yusufclaw/gateway/models.py`)

All typed events emitted over `POST /api/chat/stream` use the standard envelope:

```json
{
  "eventId": "evt_2aa8d138d37c4252b7",
  "runId": "run-dbf0a103-3b21-4e0d-a6c8-d18a1f2cfa67",
  "sessionId": "web:local:coder:conv-ux01-1",
  "agentId": "coder",
  "timestamp": "2026-10-16T13:58:44.123456+00:00",
  "sequence": 1,
  "eventType": "run.acknowledged",
  "payload": {
    "runId": "run-dbf0a103-3b21-4e0d-a6c8-d18a1f2cfa67",
    "intent": "run check",
    "estimatedSteps": 2
  }
}
```

### 3.2 Key Models

| Model | Module | Purpose |
|---|---|---|
| `AgUiEnvelope` | `gateway/models.py` | Canonical AG-UI 1.0 envelope for all SSE events |
| `MilestoneStep` | `gateway/models.py` | Progress stepper step model (`step`, `totalSteps`, `title`, `status`, `durationMs`) |
| `QuestionWidget` | `gateway/models.py` | Inline decision prompt with options and multi-select configuration |
| `WidgetResolveInput` | `gateway/models.py` | Payload for resolving inline question widgets (`questionId`, `selectedOptionIds`, `customText`) |
| `SecretRequestWidget` | `gateway/models.py` | Credential prompt sent to web client (`requestId`, `keyName`, `serviceName`, `storagePolicy`) |
| `SecretSubmitInput` | `gateway/models.py` | Payload delivering credentials from client modal to local broker |
| `CredentialBroker` | `gateway/credentials.py` | Thread-safe in-memory credential repository with 12-char SHA-256 fingerprints |

---

## 4. API Endpoints

### 4.1 Streaming & Events

- `POST /api/chat/stream`: Main turn execution endpoint. Emits typed AG-UI 1.0 events:
  - `run.acknowledged`: Immediate receipt confirmation with intent and step count.
  - `run.milestone`: Real-time milestone stepper status updates (`running`, `completed`, `failed`).
  - `message.delta`: Real-time token streaming.
  - `message.thought`: Internal chain-of-thought tokens.
  - `widget.question`: Interactive decision widget interrupt.
  - `widget.approval_card`: Verifiable ActionCard interrupt.
  - `widget.secret_request`: Masked credential prompt modal interrupt.
  - `run.completed`: Final execution summary and duration metrics.
  - *Legacy events (`RunStarted`, `progress`, `TextMessageStart`, `TextMessageContent`, `TextMessageEnd`, `RunFinished`) are preserved in parallel for backward compatibility.*
- `GET /api/events`: Global system event stream (outbox deliveries, notifications, subagent telemetry).

### 4.2 Action & Interaction Endpoints

- `GET /api/agents`: Lists active specialist personas from the role catalog (`agentId`, `name`, `model`, `description`, `allowedTools`).
- `POST /api/actions/resolve-widget`: Resolves pending inline question widgets. Duplicate calls return HTTP 400 (`already resolved`).
- `POST /api/actions/resolve-approval`: Resolves or denies verifiable ActionCards. Enforces nonce match, anti-tampering (mutated arguments fail closed), and replay protection. Aliased to `/api/chat/approval` and `/api/approval`.
- `POST /api/actions/secret-submit`: Delivers masked secret strings directly into the local `CredentialBroker`. Returns `status="stored"` and fingerprint. Plaintext is never echoed.
- `GET /api/credentials`: Lists safe metadata (`keyName`, `fingerprint`, `serviceName`, `createdAt`). Zero raw secrets exposed.

---

## 5. Web UI Implementation (`src/yusufclaw/gateway/web_page.py`)

The web interface is server-rendered with vanilla HTML5, CSS3, and JavaScript, strictly adhering to browser security and Content Security Policy (CSP):

- **Zero Third-Party Dependencies:** Pure native DOM APIs, SVG icons, and CSS variables.
- **Strict CSP Compliance:** Zero `innerHTML`, zero `localStorage`, zero `sessionStorage`. All dynamic text and markdown chunks are safely escaped via `document.createElement` and `textContent`.
- **Responsive Layout:**
  - **Left Sidebar (`#sidebar`):** Dynamic agent persona list loaded from `/api/agents`, active badge indicator, conversation session switching.
  - **Top Navigation Bar:** Active agent name pill, connection status indicator, OTel latency badge.
  - **Chat Message Area:** Immediate cognitive ack banner (`#ack-banner`), milestone stepper (`#milestones`), tool calls, and assistant message bubbles.
  - **Interactive Action Containers:**
    - Inline Question Widget (`#questions`): Clickable option chips with single/multi-selection.
    - Action Cards (`#approvals`): Live 5-minute countdown (`TTL: mm:ss`), risk badges, JSON argument inspection, and atomic Approve/Deny controls.
    - Secret Modal (`#secret-modal`): Masked password input (`••••••••`) with show/hide toggle and local broker submission.

---

## 6. Security Guarantees

1. **Memory & Context Leak Protection (UX-06):**
   - The plaintext credential is submitted directly to `POST /api/actions/secret-submit` and held in `CredentialBroker._secrets`.
   - SQLite `turns` and `memory_records` tables only record the innocuous system pill: `[Key configured: github_token (sha256: 3c9b8e1f0a2d)]`.
   - `GET /api/credentials` strips values and provides only metadata and fingerprints.
2. **Action Card Tampering Protection (UX-05):**
   - When an action card is generated, its arguments are registered with `approval_provider`.
   - Submitting an altered payload without authorization immediately fails closed with HTTP 400 (`Argument tampering detected: args hash mismatch`).
3. **Cross-Origin & Authentication Enforcement:**
   - All API endpoints strictly enforce Bearer authentication (`Authorization: Bearer <token>`).
   - Requests from untrusted origins are rejected with HTTP 403.
   - Body size limits (100KB) and strict Pydantic parsing (`extra="forbid"`) prevent payload smuggling.

---

## 7. Verification & Test Suite

The test suite is fully offline and mock-contained under `tests/python/test_ux_sprint1.py`:

```bash
uv run pytest tests/python/test_ux_sprint1.py -v
```

All 6 test cases verify individual backlog items:
- `test_ux01_agui_protocol_conformance_and_envelope_schema`: Validates AG-UI 1.0 typed envelopes, milestones, deltas, completion, and legacy backward-compatible events.
- `test_ux02_named_agents_sidebar_and_session_router`: Validates `/api/agents` catalog and session router isolation (`coder:conv-42` vs `researcher:conv-42`).
- `test_ux03_immediate_cognitive_ack_and_milestones`: Validates immediate ack and milestone stepper progression before turn completion.
- `test_ux04_inline_question_widget_resolution_lifecycle`: Validates question widget emission, selection resolution, and replay protection.
- `test_ux05_action_card_approval_editing_and_cross_channel_parity`: Validates ActionCard anti-tampering, approval/denial lifecycle, and Telegram cross-channel watcher parity.
- `test_ux06_masked_secret_request_and_local_credential_broker`: Validates credential broker storage, system pills, metadata endpoint, and absence of raw secret strings in SQLite memory.

Full repository verification:
- `uv run pytest -q`: **311 passed, 1 skipped, 1 xfailed**
- `uv run ruff check .`: Clean (0 errors)
- `uv run mypy src`: Clean (0 errors across 94 source files)
