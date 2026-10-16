"""Unit tests for YusufClaw security models, policy engine, and action card store."""

import json

from yusufclaw.security import (
    ActionCardStore,
    Grants,
    Label,
    SessionTaint,
    ToolManifest,
    Value,
    canonical_json,
    decide,
    render_card,
)


def test_canonical_json_sorting_and_compact() -> None:
    """Verify that canonical_json deterministically sorts keys and omits whitespace."""
    raw = {"b": 1, "a": 2, "nested": {"z": 9, "y": 8}, "list": [3, 2, 1]}
    canonical = canonical_json(raw)
    assert canonical == '{"a":2,"b":1,"list":[3,2,1],"nested":{"y":8,"z":9}}'

    # Verify identical parse
    assert json.loads(canonical) == raw


def test_grants_allowed_tools_and_hosts() -> None:
    """Verify capability grants enforce tool allowlists and host allowlists."""
    manifest_shell = ToolManifest(id="shell", version="1.0.0", effects=["exec"])
    manifest_web = ToolManifest(id="web_fetch", version="1.0.0", effects=["egress"])

    grants = Grants(
        allowed_tools={"web_fetch"},
        allowed_hosts={"example.com", "safe.org"},
    )

    # Shell is not in allowed_tools
    assert not grants.covers(manifest_shell, {"command": "ls"})

    # web_fetch to safe host is covered
    assert grants.covers(manifest_web, {"url": "https://example.com/api"})

    # web_fetch to unauthorized host is denied
    assert not grants.covers(manifest_web, {"url": "https://evil.attacker.com/leak"})


def test_decide_clean_session_vs_contaminated_session() -> None:
    """Verify decide allows in clean session but generates ActionCard in contaminated session."""
    manifest = ToolManifest(
        id="web_fetch",
        version="1.0.0",
        effects=["read_untrusted", "egress"],
    )

    clean_taint = SessionTaint(contaminated=False, sources=[])
    args_trusted = Value(
        v={"url": "https://example.com"},
        labels=[Label(origin="user", integrity="trusted", secrecy="public")],
    )

    # Clean session -> allow
    decision_clean = decide(manifest, args_trusted, clean_taint)
    assert decision_clean.kind == "allow"
    assert decision_clean.card is None

    # Contaminated session -> card required due to Lethal Trifecta (egress)
    dirty_taint = SessionTaint(contaminated=True, sources=["web_fetch"])
    decision_dirty = decide(manifest, args_trusted, dirty_taint)
    assert decision_dirty.kind == "card"
    assert decision_dirty.card is not None
    assert decision_dirty.card.rendered.title == "Fetch External Web URL"
    assert decision_dirty.card.rendered.exact == "https://example.com"
    assert decision_dirty.card.rendered.taint == "contaminated"
    assert "egress" in decision_dirty.card.rendered.effects


def test_decide_blocks_secret_egress() -> None:
    """Verify decide blocks outbound egress of secret-labeled values."""
    manifest = ToolManifest(
        id="web_fetch",
        version="1.0.0",
        effects=["egress"],
    )
    secret_args = Value(
        v={"url": "https://leak.internal", "token": "secret123"},
        labels=[Label(origin="env", integrity="trusted", secrecy="secret")],
    )
    decision = decide(manifest, secret_args, SessionTaint())
    assert decision.kind == "deny"
    assert decision.reason == "secret-egress"


def test_decide_untrusted_sensitive_parameter() -> None:
    """Verify decide requires ActionCard when untrusted origin sets sensitive parameters."""
    manifest = ToolManifest(
        id="file_read",
        version="1.0.0",
        effects=["read_private"],
    )
    untrusted_path_args = Value(
        v={"path": "notes.txt"},
        labels=[Label(origin="agent", integrity="untrusted", secrecy="public")],
    )
    decision = decide(manifest, untrusted_path_args, SessionTaint(contaminated=False))
    assert decision.kind == "card"
    assert decision.card is not None


def test_action_card_store_lifecycle_and_tamper_protection() -> None:
    """Verify ActionCardStore atomic consumption, replay defense, and argument tampering protection."""
    store = ActionCardStore()
    manifest = ToolManifest(id="shell", version="1.0.0", effects=["exec"])
    initial_args = {"command": "ls -la"}
    taint = SessionTaint(contaminated=False)

    card = store.create(
        manifest,
        Value(v=initial_args, labels=[]),
        taint,
    )

    # 1. First consume succeeds with matching arguments and valid nonce
    res1 = store.consume(card.approval_id, card.nonce, initial_args)
    assert res1.success is True
    assert res1.card is not None
    assert res1.card.approval_id == card.approval_id

    # 2. Replay attempt with same ID and nonce fails
    res_replay = store.consume(card.approval_id, card.nonce, initial_args)
    assert res_replay.success is False
    assert res_replay.reason is not None
    assert "Replay detected" in res_replay.reason

    # 3. Argument tampering detection on a new card
    card2 = store.create(
        manifest,
        Value(v={"command": "echo benign"}, labels=[]),
        taint,
    )
    tampered_args = {"command": "rm -rf /"}
    res_tamper = store.consume(card2.approval_id, card2.nonce, tampered_args)
    assert res_tamper.success is False
    assert res_tamper.reason is not None
    assert "Argument tampering detected" in res_tamper.reason

    # 4. Invalid nonce fails
    card3 = store.create(
        manifest,
        Value(v={"command": "pwd"}, labels=[]),
        taint,
    )
    res_nonce = store.consume(card3.approval_id, "wrong_nonce", {"command": "pwd"})
    assert res_nonce.success is False
    assert "Invalid nonce" in (res_nonce.reason or "")

    # 5. Denying a card
    deny_res = store.deny(card3.approval_id, card3.nonce)
    assert deny_res.success is True
    res_after_deny = store.consume(card3.approval_id, card3.nonce, {"command": "pwd"})
    assert res_after_deny.success is False
    assert "denied" in (res_after_deny.reason or "")


def test_render_card_formats() -> None:
    """Verify render_card produces readable exact descriptions for various tools."""
    manifest_write = ToolManifest(
        id="file_write",
        version="1.0.0",
        effects=["write_local"],
    )
    card_write = render_card(
        manifest_write,
        Value(v={"path": "test.txt", "content": "hello world"}, labels=[]),
        SessionTaint(contaminated=False),
    )
    assert card_write.rendered.title == "Write Local File"
    assert "test.txt" in card_write.rendered.exact
    assert "11 bytes" in card_write.rendered.exact
    assert card_write.rendered.reversible is False  # write_local is not reversible
