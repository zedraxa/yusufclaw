# Python Web Gateway UX Sprint 3 — Workspace Readiness Pack

Finisher deliverable that makes the AG-UI workspace operator-ready after UX-01..UX-07.

## Protocol stamps (cutting-edge, pinned)

| Surface | Version constant | Where |
|---------|------------------|--------|
| AG-UI SSE envelopes | `ag-ui/1.0` | `AG_UI_PROTOCOL_VERSION`, every `format_agui_event` |
| Agent-to-Agent cards | `a2a/1.0` | `AgentCard.protocolVersion` |
| MCP connectors | `mcp/2025-03-26` | `MCPServerConfig.protocolVersion` + hub API |

MCP remote configs also accept `oauthClientId` / `oauthRedirectUri` so OAuth apps can register the exact callback (`http://127.0.0.1:3000/oauth/callback`) and avoid `unknown client_id or unregistered redirect_uri`.

## Surfaces

### UX-08 Routines
- `GET/POST /api/automation/routines`, `DELETE /api/automation/routines/{id}`
- Strict cron validation, grants list, misfire policy, verified `nextDueAt`
- Web **Routines** tab with create form

### UX-11 MCP Connector Hub
- `GET /api/connectors`
- `POST /api/connectors/{id}/pin` / `.../quarantine`
- PinningStore-backed quarantine badges in UI

### UX-12 A2A Teammates
- `GET /api/teammates` hierarchy snapshot (cards, active depth-1 tasks, recent events)
- Live `teammate.*` SSE flush at end of turn when `TeammateStatusHook` is wired

### UX-13 Usage Meter ($50)
- `UsageMeter` with durable monthly spend (`budget:monthly:YYYY-MM`)
- `GET /api/usage` + header gauge (green / yellow≥$35 / red≥$45)
- `meter.usage` AG-UI event on stream completion; JSON chat includes `usage`

## Module map

- `src/yusufclaw/gateway/workspace.py` — AutomationStore, UsageMeter, serializers
- `src/yusufclaw/gateway/web.py` — REST + SSE wiring
- `src/yusufclaw/gateway/web_page.py` — tabs + meter UI (CSP-safe)

## Verification

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Offline suite: `tests/python/test_ux_sprint3_workspace.py`.

## Out of scope (next)

- UX-09 artifacts canvas / UX-10 live screencast (Playwright frame streaming)
- Live OAuth browser dance against third-party IdPs (fields + redirect contract are ready)
