"""Comprehensive test suite for YusufClaw specialist roles and capability matrices.

Tests:
1. CapabilityMatrix schema validation (Pydantic models, memory scopes, A2A card synthesis, budgets).
2. RoleCatalog loading, YAML validation, alias resolution, case insensitivity, error handling.
3. RoleRouter task routing across all 8 specialist domains and multi-step delegation.
4. Policy enforcement: tool allowlist denial, MCP server checks, memory scope isolation.
5. Approval gating: channel sending always mandates approval card; high-risk actions gated.
6. Web gateway AG-UI state and A2A discovery card exposure (/api/roles and SSE stream).
7. Documentation generator verification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.web import create_app
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.orchestration.models import BudgetLimits, TaskAnalysis
from yusufclaw.roles import (
    A2AAgentCard,
    CapabilityMatrix,
    MemoryScopeConfig,
    RoleCatalog,
    RoleCatalogError,
    RoleNotFoundError,
    RolePolicyEnforcer,
    RoleRouter,
    decide_with_role,
    generate_capability_matrix_markdown,
    get_role_catalog,
)
from yusufclaw.security import Label, SessionTaint, ToolManifest, Value

# ---------------------------------------------------------------------------
# 1. CapabilityMatrix Schema Tests
# ---------------------------------------------------------------------------


def test_capability_matrix_schema_valid() -> None:
    """Verify CapabilityMatrix instantiates and validates all typed fields."""
    matrix = CapabilityMatrix(
        role_id="custom_agent",
        name="Custom Agent",
        description="Testing custom specialist capabilities",
        aliases=["custom", "tester"],
        allowed_tools=["file_read", "test_runner"],
        allowed_mcp_servers=["filesystem"],
        skills=["testing"],
        model_tier="strong",
        memory_scopes=MemoryScopeConfig(read=["session", "tests"], write=["session"]),
        risk_level="medium",
        approval_actions=["deploy_action"],
        budget_limits=BudgetLimits(max_tokens_per_turn=4096, max_cost_per_turn=0.25),
        sandbox_required=True,
        sandbox_mode="auto",
        send_channels=["telegram"],
    )

    assert matrix.role_id == "custom_agent"
    assert matrix.name == "Custom Agent"
    assert matrix.model_tier == "strong"
    assert matrix.can_use_tool("file_read") is True
    assert matrix.can_use_tool("shell") is False
    assert matrix.can_use_mcp_server("filesystem") is True
    assert matrix.can_use_mcp_server("aws") is False
    assert matrix.can_read_memory("tests") is True
    assert matrix.can_read_memory("secret_repo") is False
    assert matrix.can_write_memory("session") is True
    assert matrix.can_write_memory("tests") is False
    assert matrix.requires_approval("deploy_action") is True
    assert matrix.requires_approval("file_read") is False
    assert matrix.can_send_to_channel("telegram") is True
    assert matrix.can_send_to_channel("email") is False
    assert matrix.a2a_card is not None
    assert matrix.a2a_card.name == "custom_agent"


def test_capability_matrix_flat_memory_scopes_normalization() -> None:
    """Verify flat memory_read_scopes and memory_write_scopes are normalized into memory_scopes."""
    raw = {
        "role_id": "flat_agent",
        "name": "Flat Agent",
        "description": "Testing flat memory attributes",
        "memory_read_scopes": ["inbox", "contacts"],
        "memory_write_scopes": ["triage"],
    }
    matrix = CapabilityMatrix.model_validate(raw)
    assert matrix.memory_scopes.read == ["inbox", "contacts"]
    assert matrix.memory_scopes.write == ["triage"]
    assert matrix.can_read_memory("inbox") is True
    assert matrix.can_write_memory("triage") is True


def test_capability_matrix_wildcard_memory_and_tools() -> None:
    """Verify wildcard '*' grants universal access for tools or memory scopes."""
    matrix = CapabilityMatrix(
        role_id="admin_agent",
        name="Admin Agent",
        description="Universal access agent",
        allowed_tools=["*"],
        memory_scopes=MemoryScopeConfig(read=["*"], write=["*"]),
        send_channels=["*"],
    )
    assert matrix.can_use_tool("any_tool") is True
    assert matrix.can_read_memory("any_scope") is True
    assert matrix.can_write_memory("any_scope") is True
    assert matrix.can_send_to_channel("any_channel") is True


def test_a2a_agent_card_structure() -> None:
    """Verify A2AAgentCard serialization matches A2A 1.0 specifications."""
    card = A2AAgentCard(
        name="coder",
        description="Software engineering specialist",
        version="1.0.0",
        capabilities=["code_edit", "test_runner"],
        skills=["tdd"],
    )
    dumped = card.model_dump()
    assert dumped["name"] == "coder"
    assert "code_edit" in dumped["capabilities"]
    assert "text" in dumped["input_modes"]


# ---------------------------------------------------------------------------
# 2. RoleCatalog and Loader Tests
# ---------------------------------------------------------------------------


def test_catalog_loads_all_eight_roles() -> None:
    """Verify that RoleCatalog loads all 8 required specialist agent roles."""
    catalog = RoleCatalog()
    role_ids = catalog.list_role_ids()

    expected_roles = {
        "orchestrator",
        "coder",
        "writer",
        "mail",
        "browser",
        "researcher",
        "ops",
        "scheduler",
    }
    assert expected_roles.issubset(set(role_ids)), f"Missing roles: {expected_roles - set(role_ids)}"

    roles = catalog.list_roles()
    assert len(roles) >= 8

    # Validate each role has non-empty tools or descriptions
    for r in roles:
        assert len(r.description) > 10
        assert r.model_tier in ("strong", "cheap", "local")


def test_catalog_alias_and_case_insensitive_lookup() -> None:
    """Verify aliases and case insensitivity work as expected."""
    catalog = get_role_catalog()

    # Browser via alias 'web'
    browser_role = catalog.get_role("web")
    assert browser_role.role_id == "browser"

    # Ops via alias 'devops'
    ops_role = catalog.get_role("devops")
    assert ops_role.role_id == "ops"

    # Case insensitivity
    coder_upper = catalog.get_role("CODER")
    assert coder_upper.role_id == "coder"

    # Non-existent role
    with pytest.raises(RoleNotFoundError):
        catalog.get_role("quantum_physicist")


def test_catalog_validation() -> None:
    """Verify validate_catalog() reports valid status for standard catalog."""
    catalog = RoleCatalog()
    is_valid, errors = catalog.validate_catalog()
    assert is_valid is True, f"Validation errors: {errors}"
    assert len(errors) == 0


def test_catalog_corrupt_yaml_error(tmp_path: Path) -> None:
    """Verify RoleCatalogError is raised when catalog directory contains corrupt YAML."""
    corrupt_file = tmp_path / "bad_role.yaml"
    corrupt_file.write_text("role_id: bad\n  broken: [unclosed", encoding="utf-8")

    with pytest.raises(RoleCatalogError):
        RoleCatalog(catalog_dir=tmp_path)


# ---------------------------------------------------------------------------
# 3. RoleRouter Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_router_specialist_domains() -> None:
    """Verify RoleRouter routes domain prompts to the proper specialist roles."""
    catalog = get_role_catalog()
    router = RoleRouter(catalog=catalog)

    # 1. Coder
    decision = await router.route("Refactor user auth repository and run pytest tests")
    assert decision.primary_role.role_id == "coder"

    # 2. Writer
    decision = await router.route("Draft an executive summary and report for Q3 performance")
    assert decision.primary_role.role_id == "writer"

    # 3. Mail
    decision = await router.route("Triage unread emails and draft replies to the inbox")
    assert decision.primary_role.role_id == "mail"

    # 4. Browser
    decision = await router.route("Browse python.org, scrape release notes, and take a screenshot")
    assert decision.primary_role.role_id == "browser"

    # 5. Researcher
    decision = await router.route("Search arXiv for papers on LLM agents and compare citations")
    assert decision.primary_role.role_id == "researcher"

    # 6. Ops
    decision = await router.route("Validate the AWS CDK infrastructure and deploy s3 bucket")
    assert decision.primary_role.role_id == "ops"

    # 7. Scheduler
    decision = await router.route("Schedule a calendar meeting tomorrow at 10am and set reminder")
    assert decision.primary_role.role_id == "scheduler"

    # 8. Orchestrator fallback
    decision = await router.route("Hello, how are you today?")
    assert decision.primary_role.role_id == "orchestrator"


def test_role_router_multi_step_delegation() -> None:
    """Verify multi-step tasks assign orchestrator as primary with specialists as secondaries."""
    catalog = get_role_catalog()
    router = RoleRouter(catalog=catalog)

    analysis = TaskAnalysis(
        complexity="complex",
        required_tools=["shell", "web_fetch"],
        risk="medium",
        strategy="delegate",
        subtasks=[
            "1. Search arXiv papers for background",
            "2. Write report on findings",
            "3. Refactor code and run tests",
        ],
    )
    prompt = "Search papers, write report, and update the repository code"
    decision = router.route_from_analysis(prompt, analysis)

    assert decision.primary_role.role_id == "orchestrator"
    sec_ids = [s.role_id for s in decision.secondary_roles]
    assert "researcher" in sec_ids or "writer" in sec_ids or "coder" in sec_ids


# ---------------------------------------------------------------------------
# 4. Policy Enforcement Tests
# ---------------------------------------------------------------------------


def test_enforcement_allowed_tool() -> None:
    """Verify tool in allowed_tools is permitted."""
    catalog = get_role_catalog()
    coder = catalog.get_role("coder")

    manifest = ToolManifest(id="file_read", effects=["read_private"])
    args = Value(v={"path": "src/main.py"}, labels=[Label(origin="user")])
    taint = SessionTaint(contaminated=False)

    enforcer = RolePolicyEnforcer()
    decision = enforcer.enforce(coder, manifest, args, taint)
    assert decision.kind == "allow"


def test_enforcement_denied_tool_outside_matrix() -> None:
    """Verify tool NOT in allowed_tools is denied fail-closed."""
    catalog = get_role_catalog()
    writer = catalog.get_role("writer")

    manifest = ToolManifest(id="shell", effects=["exec"])
    args = Value(v={"command": "rm -rf /"}, labels=[Label(origin="user")])
    taint = SessionTaint(contaminated=False)

    decision = decide_with_role(writer, manifest, args, taint)
    assert decision.kind == "deny"
    assert decision.reason is not None
    assert "not permitted for specialist role 'writer'" in decision.reason


def test_enforcement_mcp_server_isolation() -> None:
    """Verify MCP servers outside allowed_mcp_servers are denied."""
    catalog = get_role_catalog()
    coder = catalog.get_role("coder")

    # Allowed MCP server: git
    m_allowed = ToolManifest(id="git_diff", source="mcp")
    args_allowed = Value(v={"server": "git"}, labels=[Label(origin="user")])
    decision_allowed = decide_with_role(
        coder, m_allowed, args_allowed, SessionTaint(contaminated=False)
    )
    assert decision_allowed.kind == "allow"

    # Unauthorized MCP server: aws
    m_denied = ToolManifest(id="aws_ec2_list", source="mcp")
    args_denied = Value(v={"server": "aws"}, labels=[Label(origin="user")])
    decision_denied = decide_with_role(
        coder, m_denied, args_denied, SessionTaint(contaminated=False)
    )
    assert decision_denied.kind == "deny"
    assert "MCP server 'aws' is not permitted" in str(decision_denied.reason)


def test_enforcement_memory_scope_boundaries() -> None:
    """Verify read and write memory scope limits are strictly enforced."""
    catalog = get_role_catalog()
    coder = catalog.get_role("coder")

    # Allowed read: session
    m_recall = ToolManifest(id="recall")
    args_ok = Value(v={"scope": "session", "query": "auth"}, labels=[Label(origin="user")])
    dec_ok = decide_with_role(coder, m_recall, args_ok, SessionTaint(contaminated=False))
    assert dec_ok.kind == "allow"

    # Denied read: inbox (coder cannot inspect inbox)
    args_denied = Value(v={"scope": "inbox", "query": "auth"}, labels=[Label(origin="user")])
    dec_denied = decide_with_role(coder, m_recall, args_denied, SessionTaint(contaminated=False))
    assert dec_denied.kind == "deny"
    assert "not authorized to read memory scope 'inbox'" in str(dec_denied.reason)

    # Denied write: global (coder can only write to session/project)
    m_rem = ToolManifest(id="remember")
    args_write_denied = Value(
        v={"scope": "global", "text": "secret"}, labels=[Label(origin="user")]
    )
    dec_write_denied = decide_with_role(
        coder, m_rem, args_write_denied, SessionTaint(contaminated=False)
    )
    assert dec_write_denied.kind == "deny"
    assert "not authorized to write to memory scope 'global'" in str(dec_write_denied.reason)


def test_enforcement_channel_send_controls() -> None:
    """Verify channel delivery permissions and universal rule: send = always approval."""
    catalog = get_role_catalog()

    # Coder is forbidden from sending to any channel
    coder = catalog.get_role("coder")
    m_send = ToolManifest(id="send_message", effects=["send_message"])
    args_coder = Value(v={"channel": "telegram", "text": "hi"}, labels=[Label(origin="user")])
    dec_coder = decide_with_role(coder, m_send, args_coder, SessionTaint(contaminated=False))
    assert dec_coder.kind == "deny"
    assert "not permitted to send to channel 'telegram'" in str(dec_coder.reason)

    # Orchestrator is permitted to send to telegram, but SEND = ALWAYS APPROVAL!
    orchestrator = catalog.get_role("orchestrator")
    args_orch = Value(v={"channel": "telegram", "text": "hi"}, labels=[Label(origin="user")])
    dec_orch = decide_with_role(
        orchestrator, m_send, args_orch, SessionTaint(contaminated=False)
    )
    assert dec_orch.kind == "card"
    assert dec_orch.card is not None
    assert dec_orch.card.tool_id == "send_message"

    # Mail specialist sending email: permitted, but requires approval card
    mail_role = catalog.get_role("mail")
    m_mail = ToolManifest(id="mail_send", effects=["send_message"])
    args_mail = Value(
        v={"to": "user@example.com", "subject": "hi"}, labels=[Label(origin="user")]
    )
    dec_mail = decide_with_role(
        mail_role, m_mail, args_mail, SessionTaint(contaminated=False)
    )
    assert dec_mail.kind == "card"
    assert dec_mail.card is not None


def test_enforcement_approval_gated_actions() -> None:
    """Verify explicit high-risk actions configured in approval_actions mandate ActionCard."""
    catalog = get_role_catalog()

    # Ops deploying infrastructure
    ops = catalog.get_role("ops")
    m_deploy = ToolManifest(id="infra_deploy", effects=["write_local"])
    args_deploy = Value(v={"stack": "prod"}, labels=[Label(origin="user")])
    dec_deploy = decide_with_role(ops, m_deploy, args_deploy, SessionTaint(contaminated=False))
    assert dec_deploy.kind == "card"
    assert dec_deploy.card is not None

    # Coder git push
    coder = catalog.get_role("coder")
    m_push = ToolManifest(id="git_push", effects=["egress"])
    args_push = Value(v={"branch": "main"}, labels=[Label(origin="user")])
    dec_push = decide_with_role(coder, m_push, args_push, SessionTaint(contaminated=False))
    assert dec_push.kind == "card"

    # Browser submit / purchase
    browser = catalog.get_role("browser")
    m_submit = ToolManifest(id="browser_submit", effects=["egress"])
    args_submit = Value(v={"action": "checkout", "total": "$99"}, labels=[Label(origin="user")])
    dec_submit = decide_with_role(browser, m_submit, args_submit, SessionTaint(contaminated=False))
    assert dec_submit.kind == "card"


# ---------------------------------------------------------------------------
# 5. Web Gateway / AG-UI State Tests
# ---------------------------------------------------------------------------


class DummyAgent:
    async def handle(self, *args: Any, **kwargs: Any) -> str:
        return "dummy response"


@pytest.mark.asyncio
async def test_web_gateway_roles_endpoint() -> None:
    """Verify GET /api/roles returns all roles, capability matrices, and A2A discovery cards."""
    mem = SqliteMemory(":memory:")
    bus = MessageBus(DummyAgent(), web={"local"})
    app = create_app(bus=bus, memory=mem, token="test-token", user_id="local")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthorized check
        unauth_resp = await client.get("/api/roles")
        assert unauth_resp.status_code == 401

        # Authorized check
        auth_resp = await client.get(
            "/api/roles", headers={"Authorization": "Bearer test-token"}
        )
        assert auth_resp.status_code == 200
        roles_data = auth_resp.json()
        assert isinstance(roles_data, list)
        assert len(roles_data) >= 8

        # Verify orchestrator and coder are in response
        role_ids = [r["roleId"] for r in roles_data]
        assert "orchestrator" in role_ids
        assert "coder" in role_ids

        # Check A2A card structure in response
        for r in roles_data:
            assert "a2aCard" in r
            assert "capabilities" in r["a2aCard"]
            assert "modelTier" in r
            assert "riskLevel" in r

    mem.close()


@pytest.mark.asyncio
async def test_web_gateway_events_stream_emits_roles() -> None:
    """Verify GET /api/events SSE stream emits connected and roles events."""
    mem = SqliteMemory(":memory:")
    bus = MessageBus(DummyAgent(), web={"local"})
    app = create_app(bus=bus, memory=mem, token="test-token", user_id="local")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/events", headers={"Authorization": "Bearer test-token"}
        )
        assert resp.status_code == 200
        content = resp.text
        assert "event: connected" in content
        assert "event: roles" in content
        assert "orchestrator" in content

    mem.close()


# ---------------------------------------------------------------------------
# 6. Documentation Generator Tests
# ---------------------------------------------------------------------------


def test_capability_matrix_markdown_generation() -> None:
    """Verify markdown generator produces valid markdown with comparison table."""
    md = generate_capability_matrix_markdown()
    assert "# YusufClaw Specialist Roles — Capability Matrix" in md
    assert "| **orchestrator** |" in md
    assert "| **coder** |" in md
    assert "| **writer** |" in md
    assert "| **mail** |" in md
    assert "| **browser** |" in md
    assert "| **researcher** |" in md
    assert "| **ops** |" in md
    assert "| **scheduler** |" in md
    assert "A2A Discovery Card" in md
