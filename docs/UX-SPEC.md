# YusufClaw UX Specification: Grok-Bot Modeled User-Integration Design

**Document ID:** `docs/UX-SPEC.md`  
**Status:** Living Specification / Normative Design  
**Author:** YusufClaw Architecture & Interface Team  
**Date:** 2026-10-16  
**Target Systems:** Web UI (Primary), Telegram Bot, Terminal CLI  
**Normative Foundations:** `docs/RESEARCH.md`, `docs/RESEARCH-ASIST.md`, `docs/DESIGN-RECOMMENDATIONS.md`, `docs/ARCHITECTURE-v2.md`

---

## 1. Executive Summary & Design Vision

YusufClaw is an autonomous, personal AI assistant designed to operate continuously under the direct stewardship of a single human owner. Rather than emulating a passive conversational chatbot (which responds only synchronously to individual prompts), YusufClaw's user experience is modeled directly on the **Grok Bot assistant paradigm**:
1. **Immediate Cognitive Acknowledgment:** The agent accepts user intent instantly, releases the input thread, and shifts execution into an asynchronous background loop featuring live milestone status updates.
2. **Multi-Agent Persona Specialization:** A dedicated sidebar hosts persistent named agents (derived from the versioned role catalog in `roles/`), each maintaining isolated episodic memory, strict capability envelopes, and specialized operational heuristics.
3. **Draft-by-Default & Verifiable Human-in-the-Loop Control:** Consequential real-world actions (e.g., sending emails/messages, deploying infrastructure, executing destructive shell operations, purchasing) are never executed autonomously. Instead, the agent prepares deterministic, editable drafts and renders tamper-proof Verifiable Action Cards where the user explicitly reviews the exact payload and presses Send.
4. **Transparent Epistemic Substrate:** The user can inspect, correct, and actively forget items in the agent's structured memory, view active skills and MCP integrations, observe live sandbox/browser executions via a "Computer" pane, and track token/cost consumption in real time against a hard monthly budget ($50 AWS / local resource cap).

All interactions between the web frontend and backend are governed by the **AG-UI Protocol (Agent-to-User Interface Protocol 1.0)**, an event-driven Server-Sent Events (SSE) and bidirectional action standard. Telegram and CLI serve as first-class headless and mobile surfaces, sharing the central state and approval engine.

Every architectural and design decision in this specification is explicitly grounded in the literature and project research reports (`docs/RESEARCH.md`, `docs/RESEARCH-ASIST.md`, `docs/DESIGN-RECOMMENDATIONS.md`, `docs/ARCHITECTURE-v2.md`). Decisions lacking direct empirical research precedent are unambiguously labeled `[unbacked - proposal]`.

---

## 2. Interface Topology & Channel Strategy

```
                                  +-------------------------------------------------------------+
                                  |                     Human User (Owner)                      |
                                  +-------------------------------------------------------------+
                                     /                         |                               \
                         (Primary / Full Canvas)       (Mobile / Urgent)              (DevOps / Scripting)
                                   /                           |                                 \
                                  v                            v                                  v
                    +--------------------------+  +--------------------------+  +--------------------------+
                    |    Web UI Application    |  |     Telegram Gateway     |  |       Terminal CLI       |
                    |  - Responsive Dashboard  |  |  - Bot API / Webhooks    |  |  - Interactive Shell     |
                    |  - AG-UI 1.0 Event Stream|  |  - Inline Action Cards   |  |  - STDIN/STDOUT Pipes    |
                    |  - "Computer" Sandbox VNC|  |  - Urgent Status Alerts  |  |  - One-shot (-m) Mode    |
                    +--------------------------+  +--------------------------+  +--------------------------+
                                  \                            |                                  /
                                   \                           |                                 /
                       HTTP / AG-UI SSE              HTTPS Webhook / Poll              Local IPC / Unix Socket
                                     \                         |                                 /
                                      v                        v                                v
                                  +-------------------------------------------------------------+
                                  |                  Central Gateway Daemon                     |
                                  |  - Single Core Owner [ADR-01]                               |
                                  |  - MessageBus (Allowlist & Origin Verification)             |
                                  |  - Ephemeral Session Isolation [contextvars / ALS]          |
                                  +-------------------------------------------------------------+
                                                               |
                                                               v
                                  +-------------------------------------------------------------+
                                  |                  Core Orchestration Engine                  |
                                  |  - LangGraph State Machine & Checkpoints [ADR-02]           |
                                  |  - Deterministic Policy Engine (CaMeL / Progent) [ADR-05]   |
                                  |  - Verifiable ActionCard Ledger (VAC) [YC-05]               |
                                  |  - Durable Structured Memory (SQLite FTS5 / Vector) [ADR-03]|
                                  |  - Task Analyzer, Verifier & Budget Manager (Layer 2 & 8)   |
                                  +-------------------------------------------------------------+
```

### 2.1 Multi-Surface Coherence & Channel Roles
- **Web UI (Primary Surface):** Full-fidelity visual canvas providing multi-agent thread management, the live "Computer" sandbox viewport, dynamic artifacts canvas, secret-entry dialogs, editable memory tables, and graphical cost telemetry.
- **Telegram (Mobile Companion):** High-priority push notifications, scheduled routine deliveries, and cryptographic InlineKeyboard approval cards (`approve:<id>:<nonce>`). Free-form conversational commands map to the owner's active agent persona.
- **Terminal CLI (Developer Surface):** Local administration, headless script automation, pipeline debugging (`yusufclaw turn -m "..."`), and terminal-native approval prompts.

**Research Justification:**
- `docs/DESIGN-RECOMMENDATIONS.md` ADR-01: Centralized gateway daemon prevents state fragmentation across disparate channel adapters.
- `docs/RESEARCH.md` §4: Channel identity normalization binds `(channel, principalId, conversationId)` into immutable envelopes; group contexts never inherit private memory scopes automatically.
- `docs/RESEARCH-ASIST.md` §5.1, Levels of Autonomy (*Feng et al., 2025, arXiv:2506.12469*): Autonomy is channel-dependent (Web = "Collaborator", Telegram = "Approver", Routines = "Observer" with strictly bounded grants).

---

## 3. AG-UI Protocol Specification (v1.0)

The **AG-UI (Agent-to-User Interface) Protocol** is a strictly typed, unidirectional Server-Sent Events (SSE) stream combined with RESTful action endpoints. It standardizes how asynchronous agent execution, milestone updates, interactive decision widgets, human approvals, and sandbox telemetry are rendered in real time.

### 3.1 Transport & Wire Framing
- **Stream Endpoints:**
  - `POST /api/chat/stream`: Initiates or resumes a turn; streams real-time token deltas, milestone progress, tool dispatches, and widget interrupts for a specific conversation.
  - `GET /api/events`: Global SSE connection delivering outbox deliveries, routine notifications, approval requests, and background subagent telemetry across all agents.
- **Action Endpoints:**
  - `POST /api/actions/resolve-widget`: Submits user selections for inline question widgets.
  - `POST /api/actions/resolve-approval`: Submits cryptographic approval/denial for ActionCards.
  - `POST /api/actions/secret-submit`: Delivers masked credentials directly to the local secret broker.
- **Envelope Schema:**
```json
{
  "eventId": "evt_01J8XK39MN0QW4P9Z7R2D5",
  "runId": "run_98f1c3_4b2e8a",
  "sessionId": "web:local:agent_researcher:conv_default",
  "agentId": "agent_researcher",
  "timestamp": "2026-10-16T14:57:15.120Z",
  "sequence": 42,
  "eventType": "milestone",
  "payload": {}
}
```

### 3.2 Event Type Taxonomy

| Event Type | Direction | Payload Structure & Purpose | Research Grounding |
|---|---|---|---|
| `run.acknowledged` | Server → Client | `{ "runId": string, "intent": string, "estimatedSteps": number }`: Immediate cognitive ack acknowledging task receipt within <300ms. | `docs/RESEARCH.md` §1.2 (ReAct UX) |
| `run.milestone` | Server → Client | `{ "step": number, "totalSteps": number, "title": string, "status": "running" \| "completed" \| "failed", "detail": string }`: Live progress updates on the background plan. | `docs/RESEARCH-ASIST.md` §2.1 (*CWL / Semenov & Dorofeev, 2026*) |
| `message.delta` | Server → Client | `{ "delta": string, "accumulated": string }`: Streaming Markdown text chunks from the response synthesizer. | `docs/DESIGN-RECOMMENDATIONS.md` ADR-07 |
| `message.thought` | Server → Client | `{ "thought": string }`: Transparent rationale/planning summary (sanitized of sensitive tokens). | `docs/RESEARCH.md` §1.2 |
| `widget.question` | Server → Client | `{ "questionId": string, "prompt": string, "options": Array<{ "id": string, "label": string, "description"?: string }>, "isMultiSelect": boolean, "timeoutMs": number }`: Inline decision widget interrupting execution until user selects an option. | `docs/RESEARCH-ASIST.md` §5.1 (*Magentic-UI / Mozannar et al., 2025*) |
| `widget.approval_card` | Server → Client | Complete `ActionCardRendered` structure (title, exact command, diff, effects, taint, nonce, SHA-256, expiry). | `docs/RESEARCH-ASIST.md` §5.2 (*VAC / Irshad et al., 2026; Consent Integrity / Weng, 2026*) |
| `widget.secret_request`| Server → Client | `{ "requestId": string, "keyName": string, "description": string, "destination": string, "ephemeralPublicKey": string }`: Secure masked input form for credentials. | `docs/RESEARCH-ASIST.md` §1.3 (Willison Lethal Trifecta mitigation) |
| `artifact.created` | Server → Client | `{ "artifactId": string, "title": string, "mimeType": string, "sizeBytes": number, "previewUrl": string, "downloadUrl": string, "diff"?: string }`: Generated code, files, or reports. | `docs/ARCHITECTURE-v2.md` Layer 4 (Artifact store) |
| `computer.frame` | Server → Client | `{ "frameId": number, "timestamp": string, "mimeType": "image/jpeg" \| "image/webp", "base64Data"?: string, "domSnapshot"?: string, "activeUrl": string }`: Sandbox browser screencast / DOM view. | `docs/RESEARCH-ASIST.md` §4.2 (*UI-TARS / Qin et al., 2025; ClawBench / Zhang et al., 2026*) |
| `teammate.spawned` | Server → Client | `{ "subagentId": string, "parentRunId": string, "role": string, "task": string, "budgetTokens": number }`: Notification of child delegate spawn. | `docs/RESEARCH-ASIST.md` §2.2 (*AOrchestra / Ruan et al., 2026*) |
| `meter.usage` | Server → Client | `{ "promptTokens": number, "completionTokens": number, "totalTokens": number, "costUsd": number, "sessionSpendUsd": number, "monthlyBudgetPercent": number }`: Telemetry push after each step. | `docs/ARCHITECTURE-v2.md` Layer 8; `docs/DESIGN-RECOMMENDATIONS.md` ADR-02 |
| `run.completed` | Server → Client | `{ "runId": string, "status": "succeeded" \| "cancelled" \| "failed", "summary": string, "totalDurationMs": number }`: Finalization event closing the active stream. | `docs/DESIGN-RECOMMENDATIONS.md` ADR-02 |

---

## 4. Grok-Bot Feature Specifications

```
+---------------------------------------------------------------------------------------------------------------------------------------+
|                                                      YUSUFCLAW WEB WORKSPACE                                                          |
+----------------------+-------------------------------------------------------------------+--------------------------------------------+
| AGENT SIDEBAR        | CENTRAL CHAT & COLLABORATIVE CANVAS                               | CONTEXTUAL INSPECTION DRAWER               |
|                      |                                                                   |                                            |
| [ + New Chat ]       | > Yusuf: Deploy the new landing page to production.               | Tabs: [Computer] [Memory] [Artifacts] [Otel]|
|                      |                                                                   |                                            |
| NAMED PERSONAS:      | [Agent: 14:57:16]                                                 | +-- COMPUTER VIEW (Sandbox: bwrap) -------+|
| > YusufClaw (Core)   | Immediate Ack: "Analyzing repository diff and target server..."   | | Target: http://localhost:8080/preview    ||
| * DevSecOps (Active) |                                                                   | | [ Live Chromium Viewport / DOM ]        ||
|   Researcher (Idle)  | Milestone Stepper:                                                | | [OK] Static build generated (vite)       ||
|   Executive SRE      | [x] 1. Verify clean git working tree                              | | [OK] Assets bundled into /dist          ||
|                      | [x] 2. Run test suite: 124 passed                                 | +-----------------------------------------+|
| -------------------- | [>] 3. Awaiting production deployment approval                    |                                            |
| ROUTINES:            |                                                                   | +-- USAGE & COST METER -------------------+|
| - Nightly Consolidate| +-- VERIFIABLE ACTION CARD (YC-05) -----------------------------+ | | Turn Tokens: 1,842 (Prompt: 1,210)       ||
| - Morning RSS Digest | | Title: Deploy to Production S3/CloudFront                     | | Turn Cost:   $0.0124 USD                ||
|                      | | Exact: aws s3 sync dist/ s3://prod-assets --delete            | | Session:     $0.1420 USD                ||
| -------------------- | | Effects: [egress, write_remote] | Taint: CLEAN (trusted)      | | Monthly AWS: [||||||||..........] $14/$50||
| INTEGRATIONS (MCP):  | | Hash: 8f4e2a...c01 | Nonce: a3f1e9... | Expires: 04:52        | +-----------------------------------------+|
| [v] GitHub (Active)  | +---------------------------------------------------------------+ |                                            |
| [v] Google Drive     | | [ APPROVE & DEPLOY ]    [ EDIT DRAFT ]    [ DENY ACTION ]     | | Active Subagents (A2A):                   ||
| [!] Notion (Update!) | +---------------------------------------------------------------+ | - sub_git_verifier (Success)               ||
+----------------------+-------------------------------------------------------------------+--------------------------------------------+
```

---

### 4.1 Multiple Named Agents in Sidebar (Role Catalog Mapping)

#### A. UX & Interaction Design
- **Sidebar Organization:** A vertical navigation panel lists available agents. Each entry displays an avatar, agent name, active model badge (e.g., `Claude 3.7 Sonnet`, `Llama 3.3 70B Local`), online/idle indicator, and current context token footprint.
- **Role Catalog Integration (`roles/` on branch `py-roles`):** Agents are not arbitrary system prompts; they are instantiations of the versioned Role Catalog (`roles/<role_id>/ROLE.md`). When creating or editing an agent, the user picks from defined roles:
  - `general`: YusufClaw personal coordinator and general conversationalist.
  - `devsecops`: Software engineer, infrastructure validator, security linter.
  - `researcher`: Read-only web explorer, paper summarizer, literature searcher.
  - `executive_assistant`: Calendar, email drafter, meeting organizer, routine manager.
- **Session & Memory Isolation:** Clicking an agent swaps the active conversation pane. Each named agent maintains its own SQLite/Postgres episodic session history (`session_id = web:yusuf:<agent_id>:<conversation_id>`). A specialized agent cannot inspect another agent’s working conversational buffer unless explicitly shared via the Teamwork Substrate (Section 4.13).

#### B. AG-UI Events Used
- `agent.switched`: `{ "fromAgentId": string, "toAgentId": string, "sessionId": string, "persona": { "name": string, "role": string, "model": string } }`
- `agent.catalog_updated`: `{ "roles": Array<{ "roleId": string, "name": string, "description": string, "allowedTools": string[], "defaultModel": string }> }`

#### C. Backend Components
- `src/yusufclaw/gateway/bus.py`: Routes messages using composite keys: `json.dumps(["web", userId, f"{agentId}:{conversationId}"])`.
- `roles/<role_id>/ROLE.md`: Role configuration schema with YAML frontmatter defining permissions, tools allowlist, and persona instructions.
- `src/yusufclaw/memory/sqlite.py`: Session context isolation through `session_scope(f"{agentId}:{conversationId}")`.

#### D. Research Justification
- `docs/ARCHITECTURE-v2.md` Layer 3: Subagents/personas created with least-privilege tool grants and dedicated context windows.
- `docs/RESEARCH.md` §4: Channel and session scoping rules. Same user identity must not implicitly cross-contaminate different operational contexts or leak unvetted memory across unrelated threads.
- `docs/RESEARCH-ASIST.md` §2.1, AOrchestra (*Ruan et al., 2026, arXiv:2602.03786*): Formalizing agents as `(Instruction, Context, Tools, Model)` quadruplets rather than monolithic, multi-purpose prompts.

---

### 4.2 Immediate Acknowledgement & Milestone-Based Background Execution

#### A. UX & Interaction Design
- **Sub-300ms Cognitive Handshake:** When the user presses Enter, the UI immediately locks the input field, renders the user bubble, and appends an animated cognitive badge: *"YusufClaw received task: Analyzing requirements..."*.
- **Asynchronous Execution Stepper:** The model does not keep the user staring at a blinking dot. The UI renders an expandable **Milestone Stepper** showing real-time progress:
  - `[x] Step 1: Checked local git status (3 files modified)`
  - `[>] Step 2: Fetching API documentation from docs.anthropic.com...` (Active, animated pulse)
  - `[ ] Step 3: Propose draft patch for review`
- **Non-Blocking Background Work:** The user can navigate away from the tab, switch to another agent in the sidebar, or review history. The background task continues running. If a step requires user intervention, a badge counter lights up on the agent in the sidebar.

#### B. AG-UI Events Used
- `run.acknowledged`: `{ "runId": string, "intent": string, "estimatedSteps": number }` (emitted within 200ms of input acceptance).
- `run.milestone`: `{ "runId": string, "step": number, "title": string, "status": "running" | "completed" | "failed", "detail": string, "durationMs": number }`.

#### C. Backend Components
- `src/yusufclaw/orchestration/planner.py`: `PlanAndExecuteWorkflow` compiles the task into ordered `PlanStep` nodes.
- `src/yusufclaw/gateway/web.py`: `chat_stream` SSE handler yields `progress` and milestone payloads asynchronously without waiting for final synthesis.
- `src/yusufclaw/queue/`: Async task queue ensuring the execution loop is decoupled from the HTTP connection lifetime.

#### D. Research Justification
- `docs/RESEARCH.md` §1.1 & §1.2: ReAct (*Yao et al., 2022, arXiv:2210.03629*) and ReWOO (*Xu et al., 2023, arXiv:2305.18323*). Decoupling observation from reasoning and streaming durable state transitions keeps users informed and operations inspectable.
- `docs/RESEARCH-ASIST.md` §2.1, Structured Context Eviction / CWL (*Semenov & Dorofeev, 2026, arXiv:2606.11213*): Long-horizon agent trajectories must be demarcated into typed, dependency-linked episodes rather than amorphous token streams.

---

### 4.3 Inline Decision & Question Widgets (Options / Chips)

#### A. UX & Interaction Design
- **Structured Interruption (No Free-Text Guessing):** When the agent encounters ambiguity (e.g., "Multiple matching files found", "Select deployment target", "Choose architecture pattern"), it does not output a vague conversational question. Instead, it pauses and renders an **Inline Option Widget**:
  - Horizontal clickable chips or radio card list.
  - Each option includes a clear title, brief impact description, and a "(Recommended)" tag if applicable.
  - An optional custom text input is available as a fallback.
  - For multi-select questions, checkboxes appear alongside a prominent "Submit Selection" button.
- **State Locking:** Once clicked, the widget transitions to a read-only confirmed state showing what the user selected (`[Selected: Option B — Staging Environment]`), preventing double-submits.

#### B. AG-UI Events Used
- `widget.question`: `{ "questionId": string, "prompt": string, "isMultiSelect": boolean, "options": Array<{ "id": string, "label": string, "detail": string, "recommended": boolean }> }`
- `action.resolve_widget`: `{ "questionId": string, "selectedOptionIds": string[], "customText": string | null }` (Client → Server via `POST /api/actions/resolve-widget`).

#### C. Backend Components
- `src/yusufclaw/agent.py`: LangGraph conditional interrupt node `awaiting_input`.
- `src/yusufclaw/gateway/bus.py`: Unblocks the suspended agent graph run upon receiving the validated `questionId` matching payload.

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §5.1, Magentic-UI (*Mozannar et al., 2025, arXiv:2507.22358*): Formalizing Human-in-the-Loop coplanning through structured decision guards and discrete intervention points.
- `docs/RESEARCH-ASIST.md` §4.4, τ²-Bench (*Barres et al., 2025, arXiv:2506.07982*): Dual-control conversational environments where both agent and human take structured turns drastically reduce trajectory failure rates compared to unbounded chat.

---

### 4.4 Verifiable Action Cards & Draft-by-Default Approvals

#### A. UX & Interaction Design
- **Draft by Default:** For any consequential operation (sending email, publishing a message, deleting files, purchasing, deploying cloud infra, modifying system configs), the agent generates a complete, non-executable draft.
- **Tamper-Proof Action Card UI:** A high-contrast visual card rendered by the trusted UI layer (never rendered from raw agent markdown):
  - **Header:** Action title, affected target resource (domain, email recipient, file path).
  - **Payload Diff / Preview:** Exact unified diff of file edits, or exact email headers/body preview, or raw argv command vector (`["git", "push", "origin", "main"]`).
  - **Security Badges:** 
    - `Taint Status`: `Clean` (green) or `Contaminated` (red warning: *"This action was triggered after reading untrusted web/document content"*).
    - `Effects`: `[egress, write_local, spend]`.
  - **Controls:** Three distinct buttons:
    - `[ Approve & Execute ]`: Sends cryptographic signature and consumes approval.
    - `[ Edit Draft ]`: Expands an inline form allowing the user to mutate arguments before approval.
    - `[ Deny & Abort ]`: Rejects the action and feeds the denial back into the agent context.
- **Single-Use & Countdown Expiry:** The card displays an active countdown timer (default: 5 minutes / 300s). Once expired or consumed, buttons disable immediately to prevent replay attacks.

#### B. AG-UI Events Used
- `widget.approval_card`:
  ```json
  {
    "approvalId": "appr_01J8XK4A...",
    "nonce": "e3b0c44298fc1c149afbf4c8996fb924",
    "expiresAt": "2026-10-16T15:02:15.120Z",
    "toolId": "shell",
    "toolVersion": "1.2.0",
    "argsSha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
    "rendered": {
      "title": "Execute System Deployment",
      "exact": "kubectl apply -f k8s/production.yaml",
      "effects": ["exec", "egress"],
      "taint": "contaminated",
      "taintWarning": "Session read external content from https://malicious-pr.example.com",
      "reversible": false
    }
  }
  ```
- `action.resolve_approval`: `{ "approvalId": string, "nonce": string, "decision": "approve" | "deny", "editedArgs"?: Record<string, unknown> }`.

#### C. Backend Components
- `src/yusufclaw/security/cards.py`: `ActionCardStore` with atomic 16-byte cryptographic hex nonces, SHA-256 argument verification, and single-use consumption.
- `src/yusufclaw/security/engine.py`: `decide()` policy evaluating Monotonic Grants, Willison Lethal Trifecta, and Session Taint before generating the card.
- `src/yusufclaw/gateway/telegram.py`: Mirrors identical ActionCards to Telegram using `InlineKeyboardMarkup` (`approve:<id>:<nonce>`).

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §5.1, "Lies-in-the-Loop" / Consent Integrity (*Weng, 2026, arXiv:2606.02668*): Action cards must never be generated by LLM text narration; they must be deterministically synthesized by the trusted gateway directly from concrete tool parameters ("What You Approve Is What Executes").
- `docs/RESEARCH-ASIST.md` §5.1, The Verifiable Action Card (VAC) (*Irshad et al., 2026, arXiv:2609.18411*): Reconstructs approval info from the pending real action, binds args to cryptographic nonces, and slashes attack success from 100% to 0%.
- `docs/RESEARCH.md` §4.2: Approval binding parameters (principal, run, tool version, canonical args hash, expiry, nonce).

---

### 4.5 Masked Secret-Request Forms

#### A. UX & Interaction Design
- **Zero Secrets in Conversation History:** The agent is strictly prohibited from asking the user to paste API keys, passwords, or tokens into the regular chat input.
- **Ephemeral Secret Modal:** When a tool or integration requires a credential (e.g., `GITHUB_PERSONAL_ACCESS_TOKEN`, `NOTION_API_KEY`), the agent triggers a **Masked Secret-Request Form**:
  - Secure modal dialog containing a masked password field with a visibility toggle (`••••••••••••`).
  - Displays the requesting tool ID, target host, and storage policy: *"Stored in encrypted local keyring (`~/.yusufclaw/secrets.enc`), never written to SQLite conversation turns or transmitted to model prompts."*
  - Dedicated "Submit Secret" button.
- **Immediate In-Memory Sanitization:** The chat transcript shows only a system pill: `[Key configured: GITHUB_TOKEN (sha256: e8a2...)]`. The actual plaintext secret is routed directly to the backend secret broker.

#### B. AG-UI Events Used
- `widget.secret_request`: `{ "requestId": string, "keyName": string, "serviceName": string, "targetHost": string, "description": string }`
- `action.submit_secret`: `{ "requestId": string, "keyName": string, "ciphertext": string }` (Client → Server via TLS).

#### C. Backend Components
- `src/yusufclaw/tools/files.py`: `is_restricted_secret_path` ensures secret files are inaccessible to tool reads.
- `src/yusufclaw/security/engine.py`: FIDES secrecy label enforcement (`secrecy: 'secret'`). Secrets can never be passed as arguments to egress-capable tools.
- Credential Broker: Stores keys outside the model context in isolated environment memory.

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §1.3 & §6.1, Simon Willison Lethal Trifecta (2025): Private data/credentials must remain unreachable to untrusted content.
- `docs/RESEARCH-ASIST.md` §6.1, FIDES (*Costa et al., 2025, arXiv:2505.23643*): Information-Flow Control (IFC) mathematically prohibiting confidential (`secret`) labeled labels from flowing to `egress` tool endpoints.
- `docs/DESIGN-RECOMMENDATIONS.md` ADR-05 & §5 (P0-04, P0-05): Sandbox and secret exclusion rules.

---

### 4.6 Connector & Integration Setup via OAuth (MCP Hub)

#### A. UX & Interaction Design
- **Integrations Dashboard ("Connectors"):** A dedicated settings tab presenting available connectors: Google Workspace (Gmail, Calendar, Drive), GitHub, Notion, Slack, Telegram.
- **OAuth Flow with Pinning:**
  - Clicking "Connect" launches standard PKCE OAuth 2.0 via a local callback (`http://127.0.0.1:3000/oauth/callback`).
  - Upon authentication, the integration connects via the Model Context Protocol (MCP).
  - **Tool Pinning & Quarantine Drawer:** The user reviews the exact list of tools exposed by the MCP server (e.g., `gmail.list_messages`, `gmail.send_draft`). The UI displays the SHA-256 fingerprint of all tool descriptions.
  - If an MCP server updates its tool schema or descriptions in the background, it is automatically moved to **Quarantined** status with a yellow warning badge until the user re-authorizes the changed diff.

#### B. AG-UI Events Used
- `connector.status_changed`: `{ "connectorId": string, "status": "connected" | "disconnected" | "quarantined" | "error", "toolCount": number, "pinnedHash": string }`
- `connector.diff_detected`: `{ "connectorId": string, "oldHash": string, "newHash": string, "toolDiff": Array<{ "toolName": string, "changeType": "added" | "removed" | "description_modified", "diffText": string }> }`

#### C. Backend Components
- `src/yusufclaw/mcp/models.py`: `MCPServerConfig`, `ServerPinRecord`.
- `src/yusufclaw/mcp/pinning.py`: `compute_tools_hash` (canonical RFC 8785 JSON SHA-256 digest) and `PinningStore`.
- `src/yusufclaw/mcp/adapter.py`: Adapts external MCP tools to `GuardedTool` with `read_untrusted` default labeling. Quarantined servers fail closed with `QuarantinedServerError`.
- `src/yusufclaw/mcp/selection.py`: RAG-MCP dynamic selector when total catalog exceeds 30 tools.

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §4.1, MCP Tool Poisoning / MCPTox (*Wang et al., 2025, arXiv:2508.14925*) & MSB (*Zhang et al., 2025, arXiv:2510.15994*): Compromised or updated MCP tool descriptions hijack model steering with 72.8% attack success rate; description pinning and diff verification are mandatory.
- `docs/RESEARCH-ASIST.md` §4.1, RAG-MCP (*Gan & Sun, 2025, arXiv:2505.03275*): Reduces prompt bloat by over 50% and improves tool accuracy from 13.6% to 43.1% when navigating large tool catalogs.
- `docs/DESIGN-RECOMMENDATIONS.md` P1-07: Official MCP SDK v2.x pinning and approved server registry.

---

### 4.7 Routines Dashboard (Cron, Triggers & Daily Consolidation)

#### A. UX & Interaction Design
- **Routines Tab:** A dedicated interface displaying active recurring automations:
  - Daily Morning Briefing (`0 8 * * *`, Europe/Istanbul).
  - Nightly Memory Consolidation (`30 3 * * *`, Europe/Istanbul).
  - Pull Request Triage (Event-triggered webhook).
- **Routine Editor Card:**
  - Schedule Expression: 5-field cron builder or natural language ("Every weekday at 9am").
  - Target Agent & Persona.
  - Granted Capability Envelope (Monotonic Grants): Explicit checkboxes for permitted tools (`[x] web_fetch`, `[ ] shell`). A routine cannot be created with grants wider than the user's active session.
  - Misfire Policy Picker: `Skip` (do nothing if machine was asleep), `Coalesce` (run exactly once upon waking), `Catchup` (run all missed instances).
  - Standing Delivery Target: Private Telegram chat, Web Inbox, or CLI file.
- **Run History & Deduplication Status:** Visual log showing past routine executions, durations, token costs, and idempotency status (`deduplicated` / `executed`).

#### B. AG-UI Events Used
- `routine.triggered`: `{ "routineId": string, "name": string, "scheduledTime": string, "status": "running" }`
- `routine.completed`: `{ "routineId": string, "name": string, "reply": string, "tokensUsed": number, "costUsd": number }`

#### C. Backend Components
- `src/yusufclaw/routines/cron.py`: 5-field UTC cron evaluator with IANA timezone support.
- `src/yusufclaw/routines/scheduler.py`: Single-flight execution queue, SQLite state deduplication (`memory.get_state(f"routine:{id}:{minute}")`), and atomic execution leases.
- `src/yusufclaw/gateway/web.py`: `GET /api/routines` inbox delivery endpoint.

#### D. Research Justification
- `docs/RESEARCH.md` §3.4: OpenClaw cron automations. Persisting schedules in SQLite with explicit timezones, leased execution, and misfire policies.
- `docs/RESEARCH-ASIST.md` §4.4, π-Bench (*Zhang et al., 2026, arXiv:2605.14678*): Evaluating proactive assistant routines and autonomous scheduled tasks with cross-session continuity.
- `docs/RESEARCH-ASIST.md` §3.1, Sleep-Time Compute (*Lin et al., 2025, arXiv:2504.13171*) & LightMem (*Fang et al., 2025, arXiv:2510.18866*): Nightly offline consolidation reduces test-time query computation by ~5×.

---

### 4.8 Visible, Editable Memory Manager (Profile, Journal & Forget)

#### A. UX & Interaction Design
- **Memory Management View:** Split-pane interface giving the user complete transparency and sovereignty over their persistent identity and episodic records:
  - **Pane 1: Profile & Durable Facts:** Table of active claims (`kind = 'profile' | 'fact'`). Each row shows:
    - Subject / Predicate / Value (`User.location = Kadıköy, İstanbul`).
    - Evidence Pill: Clickable badge displaying source ID (`mem_98a2_f1c8`), creation timestamp, and origin trust level (`user_explicit` [green], `observed` [blue], `model_inferred` [orange]).
    - Validity Range: `Valid From: 2026-06-01` -> `Valid To: Present`.
    - Action Icons: `[ Edit ]` (in-place text mutation), `[ Forget ]` (red trash can).
  - **Pane 2: Episodic Journal:** Chronological event stream of past conversations, key milestones, and extracted lessons.
- **The "Forget" Flow (Deterministic Real Unlearning):**
  - Clicking `[ Forget ]` or typing `/forget <topic>` prompts a confirmation modal.
  - Upon confirmation, the backend deletes the record from `memory_records`, drops it from SQLite FTS5 (`memory_fts`), purges all embedding vector indices, writes an empty-payload tombstone to `memory_deletions`, and recalculates derived profile blocks.
  - The UI updates instantly, showing the record vaporizing with a visual animation and confirming: *"Fact deleted from active retrieval, FTS indices, and future context assemblies."*

#### B. AG-UI Events Used
- `memory.record_created`: `{ "record": MemoryRecord }`
- `memory.record_updated`: `{ "record": MemoryRecord, "previousRevision": number }`
- `memory.record_deleted`: `{ "recordId": string, "scope": string, "deletedAt": string }`

#### C. Backend Components
- `src/yusufclaw/memory/sqlite.py`: `remember`, `revise` (CAS optimistic concurrency), `forget` (tombstone generation), and `assemble_context`.
- `src/yusufclaw/memory/fts.py`: `sanitize_fts5_query` with Turkish Unicode support.
- `src/yusufclaw/memory/models.py`: Strongly typed `MemoryRecord` and `MemoryDeletionRecord`.

#### D. Research Justification
- `docs/RESEARCH.md` §2.1 & §2.3: MemGPT / Letta (*Packer et al., 2023, arXiv:2310.08560*) bounded memory blocks, and LongMemEval (*arXiv:2410.10813*) explicit knowledge updates and abstention.
- `docs/RESEARCH-ASIST.md` §3.1, Bemula (*Xie et al., 2026, arXiv:2610.18052*): Five-point benchmark for machine unlearning in agents. YusufClaw enforces tombstone-audited unlearning so deleted facts cannot be resurrected by delayed consolidation jobs.
- `docs/RESEARCH-ASIST.md` §3.1, Non-Malleable Origin-Bound Memory / TMA-NM (*Louck, 2026, arXiv:2606.24322*): Untrusted tool/web outputs cannot silently write active `trusted` claims without explicit user confirmation.

---

### 4.9 Skills Library & Packaging

#### A. UX & Interaction Design
- **Skills Catalog Modal:** Visual grid of installed procedural skills (`skills/<skill_id>/SKILL.md`):
  - Skill card displays: Name, version, description, required tools (`[shell, files]`), and security status.
  - Status Pills: `Active` (verified and locked in `skills.lock`), `Quarantined` (newly imported or hash mismatch), `Draft` (agent-generated candidate undergoing testing).
- **Skill Inspector Drawer:**
  - View YAML frontmatter conforming to the Agent Skills standard.
  - View full markdown instructions, trigger patterns, and validation test cases.
  - Action buttons: `[ Run Verification Tests ]`, `[ Deactivate ]`, `[ Promote from Quarantine ]`.

#### B. AG-UI Events Used
- `skill.status_changed`: `{ "skillId": string, "status": "active" | "quarantined" | "draft", "hash": string }`
- `skill.test_completed`: `{ "skillId": string, "passed": boolean, "score": number, "report": string }`

#### C. Backend Components
- `src/yusufclaw/skills/loader.py`: `load_skills` with depth limit (`MAX_DEPTH=8`), file size limit (32KB), and no executable script execution outside sandbox.
- `src/yusufclaw/skills/lifecycle.py`: 7-stage pipeline (`task -> solve -> test -> extract -> skill -> retest -> version git`).
- `skills.lock`: SHA-256 tamper-evident integrity pinning store.

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §4.3, Semantic Supply-Chain Attacks on Agent Skills (*Saha et al., 2026, arXiv:2605.11418*) & ClawSafety (*Wei et al., 2026, arXiv:2604.01438*): Malicious skills represent the highest-risk prompt injection vector in personal agents. Strict SHA-256 pinning, quarantine directories, and user review gates are essential.
- `docs/RESEARCH.md` §3.3, Voyager (*Wang et al., 2023, arXiv:2305.16291*): Iterative skill improvement and prompt-based procedural libraries.
- Open Agent Skills Specification (`agentskills.io`): Standardized YAML frontmatter structure.

---

### 4.10 Attachments & Interactive Artifacts Canvas

#### A. UX & Interaction Design
- **Drag-and-Drop Ingestion:** The chat input area accepts direct dragging of code files, logs, PDFs, and images. Attachments are saved to the isolated sandbox workspace (`.data/workspace/attachments/`) and tagged with unique UUIDs.
- **Split-Screen Artifacts Canvas (Grok / Claude Style):** When the agent creates or edits large deliverables (full code files, SVG diagrams, Markdown reports, HTML prototypes, data tables):
  - The chat dynamically shifts to a two-pane layout. The artifact opens in the right-hand canvas.
  - Code Editor / Viewer: Syntax highlighting, line numbers, copy button, and download link.
  - Visual Diff Viewer: Unified or side-by-side green/red diffs for modified existing files.
  - Markdown / SVG Preview: Live rendered rich preview.
  - Canvas Actions: `[ Revert to Version 1 ]`, `[ Open in Editor ]`, `[ Apply to Local Workspace ]`.

#### B. AG-UI Events Used
- `artifact.created`: `{ "artifactId": string, "title": string, "mimeType": string, "path": string, "content": string, "sizeBytes": number }`
- `artifact.updated`: `{ "artifactId": string, "revision": number, "diff": string, "content": string }`

#### C. Backend Components
- `src/yusufclaw/tools/files.py`: `file_write`, `file_read` scoped to `WORKSPACE_DIR` (`.data/workspace`).
- `src/yusufclaw/sandbox/action_log.py`: `ReversibleActionLog` tracks every file mutation with rollback hints (`delete_file`, `restore_file`).
- AWS S3 Storage Construct (`infra_py/infra_py/constructs/storage.py`): Cloud persistence for large artifacts.

#### D. Research Justification
- `docs/ARCHITECTURE-v2.md` Layer 4: Shared coordination substrate with append-only event log, artifact store, and BM25 search.
- `docs/RESEARCH.md` §1.3: Subagents and delegates return structured artifacts and status rather than flooding chat transcript tokens.

---

### 4.11 Multi-Channel Notification Engine

#### A. UX & Interaction Design
- **In-App Toast & Audio Chime:** When a background task completes or pauses for approval while the user is actively viewing the web dashboard, an unobtrusive slide-in toast appears accompanied by a discrete audio chime (toggleable in settings).
- **Web Push Notifications (Desktop PWA):** If the browser tab is minimized or in the background, a native Web Push Notification is triggered:
  - *"YusufClaw (DevSecOps): Ready for approval on Production Deployment."*
  - Clicking the notification focuses the tab directly on the pending ActionCard.
- **Telegram Redirection & Continuity:** If the web app is closed, high-priority notifications and approval requests are automatically dispatched to the owner's Telegram chat. Approving on Telegram automatically resolves the web state via the shared Gateway MessageBus.

#### B. AG-UI Events Used
- `notification.push`: `{ "notificationId": string, "severity": "info" | "warning" | "urgent", "title": string, "message": string, "actionUrl": string, "sound": boolean }`

#### C. Backend Components
- `src/yusufclaw/gateway/bus.py`: Cross-channel dispatching and event fan-out.
- `src/yusufclaw/gateway/telegram.py`: `TelegramAdapter` delivering alerts and tracking user interaction.
- `src/yusufclaw/routines/runner.py`: `ChannelRunner` handling unified message delivery across Web, Telegram, and CLI.

#### D. Research Justification
- `docs/DESIGN-RECOMMENDATIONS.md` ADR-01: Shared gateway daemon and outbox deduplication ensuring exactly-once notification delivery across surfaces.
- `docs/RESEARCH.md` §4: Channel delivery adapters and durable cursor reconciliation.

---

### 4.12 'Computer' Sandbox & Browser Live View

#### A. UX & Interaction Design
- **"Computer" Viewport Tab:** An interactive tab in the right-hand canvas providing an over-the-shoulder view of the agent's isolated execution environment:
  - **Browser Mode (Playwright):** Live screencast (1–2 fps JPEG/WebP stream) of the headless browser session as the agent navigates, fills search queries, and tests web apps.
  - **Terminal / Sandbox Mode (Bubblewrap / Rootless Container):** Live streaming pseudo-terminal showing commands executing in the isolated container, with CPU/RAM gauges and network egress monitors.
- **Interactive Intervention / Takeover [unbacked - proposal]:**
  - A button labeled `[ Take Over Control ]` allows the user to temporarily pause agent automation, manually solve a CAPTCHA or 2FA challenge directly in the browser viewport, and click `[ Resume Agent ]`.
- **Egress Firewall Overlay:** A red pill highlights blocked outbound network requests (e.g., `Blocked SSRF attempt to 169.254.169.254` or `Blocked access to RFC1918 private subnet 10.0.0.1`).

#### B. AG-UI Events Used
- `computer.frame`: `{ "mode": "browser" | "terminal", "activeUrl"?: string, "screenshotBase64"?: string, "terminalChunk"?: string, "timestamp": string }`
- `computer.security_alert`: `{ "type": "ssrf_blocked" | "secret_path_denied" | "timeout", "detail": string }`

#### C. Backend Components
- `src/yusufclaw/sandbox/runner.py`: `BubblewrapRunner` (`--unshare-net`, `--ro-bind /usr`, isolated workspace mount) and `RootlessContainerRunner`.
- `src/yusufclaw/tools/fetch.py`: `validate_host_ssrf` and DNS-resolved IP egress filter.
- `packages/tools/src/browser.ts`: Playwright browser automation wrapper.

#### D. Research Justification
- `docs/RESEARCH.md` §3.3: Playwright MCP and OS-isolated executors. The browser middleware is not a security boundary; it requires strict sandbox isolation and egress denial for private/metadata IPs.
- `docs/RESEARCH-ASIST.md` §4.2, OSWorld 2.0 (*Yuan et al., 2026, arXiv:2606.29537*) & OSGuard (*Mohammadmirzaei & Flanigan, 2026, arXiv:2606.15034*): Computer-use agents fail safely only when visual actions are monitored, execution occurs inside rootless disposable sandboxes, and consequential form submissions are gated by action cards.

---

### 4.13 Agent-to-Agent (A2A) Teammates & Dynamic Subagent DAG

#### A. UX & Interaction Design
- **Teamwork Hierarchy Canvas:** When the primary agent decomposes a complex goal into parallel subtasks, the UI expands a **Teammate Tree View**:
  - Root: `Coordinator (YusufClaw)`
    - Subagent A: `arXiv Paper Researcher` (Model: Ollama Local, Status: Fetching papers, Budget: $0.02)
    - Subagent B: `Codebase Security Linter` (Model: Bedrock Sonnet, Status: Parsing AST, Budget: $0.05)
- **Data vs. Instruction Channel Segregation:** The UI visually differentiates between trusted user instructions (blue pills) and untrusted data exchanged between subagents (striped yellow pills).
- **Subagent Concurrency Governance:**
  - Maximum subagent nesting depth is strictly locked to **Depth = 1** (child subagents cannot spawn further grandchildren).
  - Maximum concurrent workers = 3.
  - Subagents cannot initiate external communications or approve their own actions.

#### B. AG-UI Events Used
- `teammate.spawned`: `{ "subagentId": string, "parentRunId": string, "role": string, "task": string, "tools": string[], "maxCost": number }`
- `teammate.step`: `{ "subagentId": string, "step": number, "summary": string, "tokens": number }`
- `teammate.finished`: `{ "subagentId": string, "status": "success" | "failed", "artifactRef": string, "costUsd": number }`

#### C. Backend Components
- `src/yusufclaw/orchestration/subagent.py`: `SubagentRunner` with least-privilege tool intersection, Depth-1 enforcement, and budget inheritance.
- `src/yusufclaw/orchestration/task_analyzer.py`: `TaskAnalyzer` determining when tasks benefit from parallel subagents vs. single-agent loops.

#### D. Research Justification
- `docs/RESEARCH-ASIST.md` §2.1, MAST (*Cemri et al., 2025, arXiv:2503.13657*): 14 failure modes in multi-agent LLM systems; unconstrained agent conversation creates coordination collapse. YusufClaw restricts delegation to depth-1 worker jobs with strict output schemas.
- `docs/RESEARCH-ASIST.md` §6.1, Can CaMeLs Talk? (*Peters-Gill et al., 2026, arXiv:2610.05640*): Prompt injection vulnerabilities multiply across multi-agent boundaries; data channels must remain strictly segregated from instruction channels.
- `docs/RESEARCH-ASIST.md` §2.1, Asynchronous Software Engineering Agents / CAID (*Geng & Neubig, 2026, arXiv:2603.21489*): Parallel coding delegates require git worktree isolation and test gates before merging into the main branch.

---

### 4.14 Real-Time Usage, Cost & Telemetry Meter

#### A. UX & Interaction Design
- **Persistent Header Telemetry Widget:** Located in the top-right header of the web application:
  - **Turn Metrics:** Instantaneous token count (`1,420 tokens`) and elapsed latency (`1.8s`).
  - **Turn Cost:** Real-time dollar cost calculated via provider token pricing (`$0.0084 USD`).
  - **Monthly Budget Gauge:** Visual progress bar tracking cumulative spending against the $50.00 AWS/API monthly limit:
    - Green: `< $35.00`
    - Yellow: `$35.00 – $45.00`
    - Red Alert: `> $45.00` (Displays warning that non-critical subagents and heavy workers are paused).
- **Telemetry Drawer (Deep Dive):** Clicking the gauge opens an OpenTelemetry trace view:
  - Gantt chart of the turn: `Model Complete (820ms)` -> `Policy Check (2ms)` -> `Tool: file_read (14ms)` -> `Model Synthesis (640ms)`.
  - Token breakdown: Prompt tokens vs. Completion tokens vs. Cached tokens.

#### B. AG-UI Events Used
- `meter.usage`:
  ```json
  {
    "turnTokens": { "prompt": 1210, "completion": 240, "total": 1450 },
    "turnCostUsd": 0.0084,
    "sessionTokensTotal": 18450,
    "sessionCostUsd": 0.1240,
    "monthlySpendUsd": 14.25,
    "monthlyLimitUsd": 50.00,
    "budgetPercent": 28.5
  }
  ```

#### C. Backend Components
- `src/yusufclaw/telemetry/`: OpenTelemetry metrics (`agent.turn.duration`, `model.tokens.total`, `model.call.duration`, `approval.wait.duration`).
- `src/yusufclaw/orchestration/budget.py`: `BudgetManager` tracking turn, session, and monthly spending with graceful degradation when ceilings are hit.
- `infra_py/infra_py/constructs/cost_budget.py`: AWS Budgets $50/month hard ceiling and SNS alert construct.

#### D. Research Justification
- `docs/ARCHITECTURE-v2.md` Layer 2 & 8: Budget-aware meta-orchestrator enforcing token/$ caps per task and honoring the hard $50/mo AWS limit; OpenTelemetry tracing across all agents.
- `docs/RESEARCH.md` §5 (Priority 0/1): Deterministic budget accounting and runaway loop termination.
- `docs/RESEARCH-ASIST.md` §2.1, Beyond Token Savings (*Satish et al., 2026, arXiv:2609.32961*): Reporting both end-to-end latency and exact dollar cost, separating user waiting time from compute time.

---

## 5. Security & Human Agency Governance Model

YusufClaw’s user integration is built around a rigorous threat model that rejects prompt-based disclaimers in favor of architectural defenses:

```
+---------------------------------------------------------------------------------------------------+
|                                  DETERMINISTIC SECURITY BOUNDARY                                  |
+---------------------------------------------------------------------------------------------------+
|  1. Monotonic Capability Grants (Progent)                                                         |
|     - Tools can only be executed if explicitly permitted by user grant envelope.                  |
|     - Permissions can automatically narrow, but can NEVER expand without explicit human card.    |
|                                                                                                   |
|  2. Information-Flow & Taint Tracking (CaMeL / FIDES / SkillGuard)                                 |
|     - Inputs from web_fetch, browser, and MCP are labeled integrity: 'untrusted'.                |
|     - Once a session ingests untrusted data, SessionTaint transitions to 'contaminated'.          |
|     - Contaminated sessions CANNOT execute egress, send_message, spend, or exec without a VAC card.|
|                                                                                                   |
|  3. Consent Integrity & What You Approve Is What Executes (VAC / Lies-in-the-Loop)                |
|     - ActionCards are synthesized deterministically from real validated arguments.               |
|     - Bound to 16-byte nonce, 5-minute TTL, and SHA-256 canonical JSON digest.                    |
|     - Replay attacks and argument mutations are rejected fail-closed.                            |
|                                                                                                   |
|  4. Zero Secrets in Epistemic Memory                                                              |
|     - .env*, *.sqlite*, ~/.aws*, and private keys are barred from tool reads and search index.    |
+---------------------------------------------------------------------------------------------------+
```

**Research Citations:**
- *Progent: Securing AI Agents with Privilege Control* (Shi et al., 2025, arXiv:2504.11703)
- *Defeating Prompt Injections by Design / CaMeL* (Debenedetti et al., 2025, arXiv:2503.18813)
- *FIDES: Information-Flow Control for AI Agents* (Costa et al., 2025, arXiv:2505.23643)
- *The Verifiable Action Card* (Irshad et al., 2026, arXiv:2609.18411)
- *What You Approve Is What Executes* (Weng, 2026, arXiv:2606.02668)

---

## 6. Cross-Channel Parity Matrix

The following matrix documents feature availability, representation, and graceful degradation strategies across YusufClaw’s three supported surfaces:

| Feature | Web UI (Primary Canvas) | Telegram (Mobile Companion) | Terminal CLI (Developer) | Fallback / Degradation Strategy |
|---|---|---|---|---|
| **Named Agents Sidebar** | Full visual sidebar, persona switching, active badges | `/agent <name>` command, bot command menu | `yusufclaw chat --agent <name>` | CLI/Telegram maintain isolated session keys per agent. |
| **Immediate Ack & Milestones** | Inline cognitive badge + animated milestone stepper | Initial ack message, edited via Bot API rate limits (max 1 edit/sec) | Instant ack line, animated terminal spinner with milestone lines | Telegram throttles edits to prevent 429 rate limit errors. |
| **Inline Question Widgets** | Clickable chips, radio list, multi-select checkboxes | Telegram InlineKeyboard buttons (`opt:<id>`) | Numbered interactive terminal prompt (`[1] Option A, [2] Option B`) | Non-interactive CLI runs fail fast if a required question is encountered. |
| **Verifiable Action Cards** | Visual security card, diff preview, countdown timer | Localized alert message + InlineKeyboard (`approve:<id>:<nonce>`) | Formatted ANSI box with diff preview and `Type yes to approve: ` prompt | Telegram separates model claim from exact command; CLI requires explicit typed confirmation. |
| **Masked Secret Forms** | Secure masked modal dialog (`••••••••`) | One-time self-destructing secret prompt or redirect to Web UI | Hidden terminal input (`getpass`) | Plaintext secrets are never echoed or recorded in transcripts. |
| **Connector Setup (MCP)** | Visual OAuth dashboard, tool diff viewer | Notification of connected status; link to web dashboard for OAuth | CLI command: `yusufclaw mcp auth <server>` launching browser | OAuth redirect requires browser interaction; CLI spawns local listener. |
| **Routines Dashboard** | Cron builder, calendar view, execution history | Status alerts, `/routines` list command | `yusufclaw routines list`, `yusufclaw routines run <id>` | Background scheduler executes independently of open UI clients. |
| **Editable Memory Manager** | Split-pane profile table and episodic journal | `/memory` search, `/forget <query>` | `yusufclaw memory list`, `yusufclaw memory forget <id>` | All interfaces route to identical SQLite memory methods. |
| **Skills Library** | Visual card grid, frontmatter inspector | `/skills` list command | `yusufclaw skills list`, `yusufclaw skills test <id>` | Skills editing is restricted to local files; UI provides read/test interface. |
| **Artifacts Canvas** | Side-by-side editor, code highlighting, visual diffs | File attachment upload (document/photo) with caption summary | File paths written to workspace; diffs output to terminal | Telegram receives rendered file attachments (e.g. `.pdf`, `.png`, `.py`). |
| **Sandbox / Computer View** | Live JPEG/DOM screencast + network egress monitor | Screenshot snapshot sent as photo on milestone | Status summary: `Browser at https://... (200 OK)` | Live video screencast is Web-only; Telegram receives periodic keyframes. |
| **A2A Teammate View** | Interactive tree/DAG canvas with subagent cards | Collapsed bullet summary: `Subagent 1: Succeeded` | Indented tree log output | Full DAG hierarchy is Web-only. |
| **Usage & Cost Meter** | Real-time gauge, monthly progress bar, OTel Gantt | `/usage` command returning turn/session/month summary | Turn summary printed to stderr (`[1,420 tokens | $0.0084 USD]`) | Hard budget enforcer terminates runs regardless of interface used. |

---

## 7. Implementation Backlog (Ordered, Small Tasks)

The following backlog defines an incremental, dependency-ordered engineering roadmap for delivering the full user-integration design. Each task is scoped for focused execution without breaking existing test suites.

```
[UX-01: AG-UI Protocol Foundation]
  |
  +---> [UX-02: Named Agents Sidebar & State Engine]
  |       |
  |       +---> [UX-03: Immediate Ack & Background Milestone Stepper]
  |       |
  |       +---> [UX-04: Inline Decision & Question Widgets]
  |
  +---> [UX-05: Verifiable Action Cards UI & Telegram Parity]
  |       |
  |       +---> [UX-06: Masked Secret-Request Modal & Broker]
  |
  +---> [UX-07: Memory Inspection & Forget Canvas]
  |       |
  |       +---> [UX-08: Routines & Automation Dashboard]
  |
  +---> [UX-09: Artifacts Split-Screen Canvas & Diff Viewer]
  |       |
  |       +---> [UX-10: Computer View: Live Sandbox & Browser Screencast]
  |
  +---> [UX-11: MCP Connector Hub & Tool Pinning Inspector]
  |
  +---> [UX-12: A2A Teammates Hierarchy Visualizer]
  |
  +---> [UX-13: Usage & Telemetry Meter Gauge ($50 Ceiling)]
```

### Sprint 1: Protocol & Core Interaction Foundations

#### UX-01: AG-UI 1.0 Protocol & Server-Sent Events Pipeline
- **Deliverable:** Expand `src/yusufclaw/gateway/web.py` to stream typed AG-UI 1.0 JSON event frames (`run.acknowledged`, `run.milestone`, `message.delta`, `message.thought`, `done`).
- **Dependencies:** `py-gateway` base endpoints.
- **Acceptance Gate:** Unit test confirms SSE stream adheres to AG-UI schema and delivers sub-300ms acknowledgement events.

#### UX-02: Named Agents Sidebar & Session Router
- **Deliverable:** Implement Web UI navigation sidebar rendering named personas from `roles/`; wire session isolation keys (`web:userId:agentId:conversationId`).
- **Dependencies:** UX-01, `roles/` catalog.
- **Acceptance Gate:** Switching agents updates conversation buffer without cross-session memory leakage.

#### UX-03: Immediate Acknowledgement & Milestone Stepper Widget
- **Deliverable:** Build UI stepper component consuming `run.milestone` events; non-blocking input release allowing navigation while background runs proceed.
- **Dependencies:** UX-01, UX-02.
- **Acceptance Gate:** Long-running multi-step tools update the UI dynamically without hanging the HTTP request.

#### UX-04: Inline Decision & Question Widgets
- **Deliverable:** Build client-side chip/option widget rendering `widget.question` events and submitting `POST /api/actions/resolve-widget`; connect to LangGraph `awaiting_input` state.
- **Dependencies:** UX-01, `src/yusufclaw/agent.py`.
- **Acceptance Gate:** Selecting an option immediately unblocks agent execution; duplicate submits are rejected.

---

### Sprint 2: Trust, Governance & Secret Safeguards

#### UX-05: Verifiable Action Cards UI & Cross-Channel Consumption
- **Deliverable:** Full visual implementation of ActionCard component in Web UI with exact command diff, taint status badge, 5-minute countdown, and atomic `approve`/`deny` buttons matching `src/yusufclaw/security/cards.py`.
- **Dependencies:** UX-01, `py-security` (YC-05).
- **Acceptance Gate:** Expired nonces or mutated payloads fail closed; approving on Telegram updates Web UI state in real time.

#### UX-06: Masked Secret-Request Modal & Local Credential Broker
- **Deliverable:** Web dialog handling `widget.secret_request` events; routes masked input directly to local keyring without writing to chat turns or model context.
- **Dependencies:** UX-05.
- **Acceptance Gate:** Secret value never appears in SQLite `turns` or `memory_records` tables.

#### UX-07: Transparent Memory Inspection & Deterministic Forget Canvas
- **Deliverable:** Web UI split-pane showing Profile facts, episodic journal, source evidence IDs, and active `/forget` trigger.
- **Dependencies:** `py-memory` (YC-07, YC-08).
- **Acceptance Gate:** Forgetting a fact instantly removes it from the UI table, deletes FTS5 index rows, and logs a tombstone.

---

### Sprint 3: Automation, Sandboxing & Deep Capabilities

#### UX-08: Routines & Automation Dashboard
- **Deliverable:** Routines tab in Web UI providing visual cron builder, capability grant checklists, and delivery targets.
- **Dependencies:** UX-01, `py-skills` (YC-18).
- **Acceptance Gate:** Creating a routine produces a validated JSON entry with strict grants and verified next-due timestamp.

#### UX-09: Interactive Artifacts Canvas & Diff Viewer
- **Deliverable:** Split-screen canvas rendering code files, Markdown deliverables, and unified diffs with version history.
- **Dependencies:** UX-01, `src/yusufclaw/tools/files.py`.
- **Acceptance Gate:** Edits generated by tools render side-by-side with line numbers and one-click copy/download.

#### UX-10: Computer View: Sandbox & Browser Screencast
- **Deliverable:** "Computer" viewport tab streaming Playwright screencast frames and Bubblewrap execution console logs with egress alerts.
- **Dependencies:** UX-09, `src/yusufclaw/sandbox/runner.py`.
- **Acceptance Gate:** Blocked SSRF attempts display immediate red security pills in the viewport.

#### UX-11: MCP Connector Hub & Tool Pinning Inspector
- **Deliverable:** Settings interface for managing external MCP servers, reviewing tool lists, and approving description diffs upon quarantine.
- **Dependencies:** `py-mcp` (YC-16).
- **Acceptance Gate:** Hash changes trigger visual quarantine badges and prevent tool execution until confirmed.

#### UX-12: A2A Teammates Hierarchy Visualizer
- **Deliverable:** Interactive subagent tree component displaying depth-1 delegate tasks, active tools, and segregated data channels.
- **Dependencies:** `py-orch` (YC-20).
- **Acceptance Gate:** Displays active subagent status and enforces budget caps per child worker.

#### UX-13: Usage, Cost & Telemetry Meter ($50 Budget Enforcer)
- **Deliverable:** Header telemetry widget rendering token counts, turn cost ($ USD), and monthly AWS budget gauge with OTel drilldown drawer.
- **Dependencies:** `py-otel`, `src/yusufclaw/orchestration/budget.py`.
- **Acceptance Gate:** Simulating a spend exceeding $45.00 triggers visual yellow/red warnings and disables non-essential subagent delegation.

---

## 8. Normative Reference & Evidence Mapping

This specification is strictly derived from the following peer-reviewed literature and project research reports:

### 8.1 Foundational Architecture Reports
- **`docs/ARCHITECTURE-v2.md`:** Layered architecture (Layers 1–9), single-agent starting baseline with adaptive orchestration, OTel tracing, $50 monthly AWS budget limit, and rootless sandboxing.
- **`docs/DESIGN-RECOMMENDATIONS.md`:** Architectural Decision Records (ADR-01 through ADR-10), SQLite persistence schemas, P0/P1 roadmap tickets, and contract definitions (`RunStatus`, `MessageEnvelope`, `ApprovalBinding`, `MemoryClaim`).
- **`docs/RESEARCH.md`:** Literature foundation for ReAct (*Yao et al., 2022*), MemGPT (*Packer et al., 2023*), Voyager (*Wang et al., 2023*), SQLite FTS5 lexical indexing, and OpenClaw gateway architecture.
- **`docs/RESEARCH-ASIST.md`:** 2025–2026 personal agent empirical findings, CIK taxonomy, lethal trifecta mitigations, VAC approval cards, and YC-01 through YC-30 ticket matrix.

### 8.2 Primary Scientific Literature Citations
1. **Consent Integrity & Action Cards:**
   - *Weng, X. (2026).* "What You Approve Is What Executes: Consent Integrity for Black-Box LLM Agents." arXiv:2606.02668.
   - *Irshad, H. et al. (2026).* "The Verifiable Action Card: Trustworthy Human-in-the-Loop Control for Secure Autonomous Agents." arXiv:2609.18411.
   - *Chen, C. et al. (2026).* "Comparing Human Oversight Strategies for Computer-Use Agents." arXiv:2604.04918.
2. **Information-Flow Control & Injection Defenses:**
   - *Debenedetti, E. et al. (2025).* "Defeating Prompt Injections by Design [CaMeL]." arXiv:2503.18813.
   - *Costa, M. et al. (2025).* "Securing AI Agents with Information-Flow Control [FIDES]." arXiv:2505.23643.
   - *Shi, T. et al. (2025).* "Progent: Securing AI Agents with Privilege Control." arXiv:2504.11703.
   - *Xiong, W. et al. (2026).* "Reachability-Based Capability Confinement for LLM Agents [SkillGuard]." arXiv:2608.30041.
   - *Wang, P. et al. (2026).* "Aligning Provenance with Authorization: A Dual-Graph Defense [AuthGraph]." arXiv:2605.26497.
   - *Peters-Gill, J. et al. (2026).* "Can CaMeLs Talk? Securing Multi-Agent Systems Against Indirect Prompt Injection." arXiv:2610.05640.
3. **Memory, Forgetting & Consolidation:**
   - *Xie, B. et al. (2026).* "Can Agents Forget When Asked? A Benchmark for Memory Unlearning in LLM Agents [Bemula]." arXiv:2610.18052.
   - *Louck, Y. (2026).* "Securing LLM-Agent Long-Term Memory Against Poisoning [TMA-NM]." arXiv:2606.24322.
   - *Yang, T. et al. (2026).* "TRUSTMEM: Learning Trustworthy Memory Consolidation for LLM Agents." arXiv:2606.25161.
   - *Fang, J. et al. (2025).* "LightMem: Lightweight and Efficient Memory-Augmented Generation." arXiv:2510.18866.
   - *Lin, K. et al. (2025).* "Sleep-time Compute: Beyond Inference Scaling at Test-time." arXiv:2504.13171.
   - *Rusu, T. et al. (2026).* "Selective Forgetting: A Graph-Based Memory Framework." arXiv:2608.28978.
4. **Tool Use, MCP & Computer Use:**
   - *Wang, Z. et al. (2025).* "MCPTox: A Benchmark for Tool Poisoning Attack on Real-World MCP Servers." arXiv:2508.14925.
   - *Zhang, D. et al. (2025).* "MCP Security Bench (MSB)." arXiv:2510.15994.
   - *Gan, T. & Sun, Q. (2025).* "RAG-MCP: Mitigating Prompt Bloat in LLM Tool Selection." arXiv:2505.03275.
   - *Mohammadmirzaei, M. & Flanigan, J. (2026).* "OSGuard: A Benchmark for Safety in Computer-Use Agents." arXiv:2606.15034.
   - *Yuan, M. et al. (2026).* "OSWorld 2.0: Benchmarking Computer Use Agents on Long-Horizon Real-World Tasks." arXiv:2606.29537.
5. **Multi-Agent Orchestration & Context Eviction:**
   - *Ruan, J. et al. (2026).* "AOrchestra: Automating Sub-Agent Creation for Agentic Orchestration." arXiv:2602.03786.
   - *Cemri, M. et al. (2025).* "Why Do Multi-Agent LLM Systems Fail? [MAST]." arXiv:2503.13657.
   - *Geng, J. & Neubig, G. (2026).* "Effective Strategies for Asynchronous Software Engineering Agents [CAID]." arXiv:2603.21489.
   - *Semenov, A. & Dorofeev, S. (2026).* "Beyond Compaction: Structured Context Eviction for Long-Horizon Agents [CWL]." arXiv:2606.11213.
   - *Satish, R. et al. (2026).* "Beyond Token Savings: A Systematic Study of Context Compression." arXiv:2609.32961.
6. **Non-arXiv Technical Standards:**
   - *Willison, S. (2025).* "The lethal trifecta for AI agents: private data, untrusted content, and external communication."
   - *Model Context Protocol Specification (2026-07-28).* Anthropic / Linux Foundation.
   - *Agent Skills Open Specification (2025).* agentskills.io.
