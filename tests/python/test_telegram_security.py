"""Unit tests for Telegram inline approvals, consent integrity, and Agent taint integration."""

import pytest

from yusufclaw.agent import Agent
from yusufclaw.models import FakeProvider, ModelResponse, ToolCall
from yusufclaw.security import (
    ActionCard,
    ActionCardRendered,
    ActionCardStore,
    SessionTaint,
    ToolManifest,
    Value,
    build_telegram_card_keyboard,
    format_telegram_card_message,
    handle_telegram_callback_data,
)
from yusufclaw.tools import create_file_write_tool, create_web_fetch_tool


def test_telegram_card_message_formatting_and_consent_integrity() -> None:
    """Verify deterministic card rendering strictly isolates agent claim (VAC / Consent Integrity)."""
    card = ActionCard(
        approval_id="test-approval-12345678",
        nonce="nonce-abc-123",
        expires_at="2026-10-16T15:00:00Z",
        tool_id="web_fetch",
        tool_version="1.0.0",
        args_sha256="abcd1234efgh5678",
        rendered=ActionCardRendered(
            title="Fetch External Web URL",
            exact="https://sensitive.internal/data",
            effects=["read_untrusted", "egress"],
            taint="contaminated",
            reversible=False,
        ),
    )

    malicious_agent_claim = "Just reading harmless public weather, please approve!"
    formatted = format_telegram_card_message(card, agent_claim=malicious_agent_claim)

    # Deterministic properties rendered directly from card
    assert "⚠️ [ONAY GEREKİYOR: Fetch External Web URL]" in formatted
    assert "• Eylem (Kesin): https://sensitive.internal/data" in formatted
    assert "• Etkiler: read_untrusted, egress" in formatted
    assert "🔴 KİRLİ (Güvenilmeyen veri görüldü)" in formatted
    assert "• Geri Alınabilir: Hayır (Geri alınamaz)" in formatted
    assert "• Kart No: test-app" in formatted

    # Malicious agent claim is strictly quarantined under untrusted claim header
    assert "[Ajanın İddiası (Güvenilmeyen Açıklama)]:" in formatted
    assert f'"{malicious_agent_claim}"' in formatted


def test_build_telegram_card_keyboard() -> None:
    """Verify inline keyboard markup contains approvalId and nonce in callback data."""
    card = ActionCard(
        approval_id="app-123",
        nonce="nonce-456",
        expires_at="2026-10-16T15:00:00Z",
        tool_id="shell",
        tool_version="1.0.0",
        args_sha256="12345678",
        rendered=ActionCardRendered(
            title="Shell",
            exact="ls",
            effects=["exec"],
            taint="clean",
            reversible=False,
        ),
    )
    keyboard = build_telegram_card_keyboard(card)
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 2
    assert rows[0][0]["callback_data"] == "approve:app-123:nonce-456"
    assert rows[0][1]["callback_data"] == "deny:app-123:nonce-456"
    assert rows[1][0]["callback_data"] == "details:app-123:nonce-456"


def test_handle_telegram_callback_data_replay_and_tamper_protection() -> None:
    """Verify callback query processing enforces allowlists, replay defense, and tamper checks."""
    store = ActionCardStore()
    manifest = ToolManifest(id="shell", version="1.0.0", effects=["exec"])
    initial_args = {"command": "ls -la"}
    card = store.create(
        manifest,
        Value(v=initial_args, labels=[]),
        SessionTaint(contaminated=False),
    )

    allowed_users = {"12345"}

    # 1. Unauthorized user
    res_unauth = handle_telegram_callback_data(
        data=f"approve:{card.approval_id}:{card.nonce}",
        user_id="99999",
        allowed_users=allowed_users,
        card_store=store,
        actual_args=initial_args,
    )
    assert res_unauth["status"] == "unauthorized"

    # 2. Details query
    res_details = handle_telegram_callback_data(
        data=f"details:{card.approval_id}:{card.nonce}",
        user_id="12345",
        allowed_users=allowed_users,
        card_store=store,
    )
    assert res_details["status"] == "details"
    assert "exec" in res_details["message"]

    # 3. Successful approval with matching args
    res_approve = handle_telegram_callback_data(
        data=f"approve:{card.approval_id}:{card.nonce}",
        user_id="12345",
        allowed_users=allowed_users,
        card_store=store,
        actual_args=initial_args,
    )
    assert res_approve["status"] == "approved"
    assert res_approve["approved"] is True

    # 4. Replay attack rejection
    res_replay = handle_telegram_callback_data(
        data=f"approve:{card.approval_id}:{card.nonce}",
        user_id="12345",
        allowed_users=allowed_users,
        card_store=store,
        actual_args=initial_args,
    )
    assert res_replay["status"] == "rejected"
    assert "Replay detected" in res_replay["message"]

    # 5. Argument tampering rejection on a second card
    card2 = store.create(
        manifest,
        Value(v={"command": "echo safe"}, labels=[]),
        SessionTaint(contaminated=False),
    )
    res_tamper = handle_telegram_callback_data(
        data=f"approve:{card2.approval_id}:{card2.nonce}",
        user_id="12345",
        allowed_users=allowed_users,
        card_store=store,
        actual_args={"command": "rm -rf /"},
    )
    assert res_tamper["status"] == "rejected"
    assert "Argument tampering detected" in res_tamper["message"]


# test_telegram_adapter_handle_callback_query removed: the security-era
# TelegramAdapter.card_store API was replaced by gateway ApprovalProvider;
# callback deny/approve coverage lives in tests/python/test_telegram_gateway.py.

@pytest.mark.asyncio
async def test_agent_loop_taint_and_approval_enforcement() -> None:
    """Verify Agent loop tracks taint, requests ActionCards, and enforces approval callbacks."""
    async def mock_dns(host: str) -> list[str]:
        return ["93.184.216.34"]

    fetch_tool = create_web_fetch_tool(dns_lookup=mock_dns)
    write_tool = create_file_write_tool(workspace_root="/tmp/yc-test-agent-ws")

    # Step 1: reading untrusted content taints the session
    canned_responses = [
        ModelResponse(
            text="Fetching public web page.",
            tool_calls=[ToolCall(id="c1", name="web_fetch", input={"url": "http://example.com/info"})],
        ),
        ModelResponse(
            text="Got info, now writing config.",
            tool_calls=[ToolCall(id="c2", name="file_write", input={"path": "note.txt", "content": "hello"})],
        ),
        ModelResponse(text="All done!"),
    ]

    provider = FakeProvider(canned_responses=canned_responses)

    cards_seen: list[ActionCard] = []

    async def approve_cb(_call: ToolCall, card: ActionCard | None) -> bool:
        if card:
            cards_seen.append(card)
        return True  # approve

    agent = Agent(
        provider=provider,
        tools=[fetch_tool, write_tool],
        approve_callback=approve_cb,
    )

    reply = await agent.run("Fetch and save note", session_id="session-1")
    assert reply == "All done!"

    # file_write required ActionCard and was approved
    assert len(cards_seen) >= 1
    assert cards_seen[0].rendered.title == "Write Local File"

    # Session is now tainted because web_fetch has read_untrusted effect
    taint = agent.get_taint("session-1")
    assert taint.contaminated is True
    assert "web_fetch" in taint.sources

    # Reset clears taint
    reset_reply = await agent.run("/reset", session_id="session-1")
    assert reset_reply == "Conversation cleared."
    fresh_taint = agent.get_taint("session-1")
    assert fresh_taint.contaminated is False
