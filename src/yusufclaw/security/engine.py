"""Deterministic security policy engine for YusufClaw.

Implements pre-execution decision logic:
- Canonical JSON serialization for tamper-evident hashing.
- Monotonic capability grant checks (Progent).
- Secrecy violation denial (FIDES: secret-labeled data cannot egress).
- Lethal Trifecta defense: contaminated sessions require human ActionCard approval
  for egress, send_message, spend, or exec.
- Untrusted parameter protection: untrusted input targeting sensitive parameters
  requires approval cards.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from yusufclaw.security.models import (
    ActionCard,
    ActionCardRendered,
    PolicyDecision,
    SessionTaint,
    ToolManifest,
    Value,
)


def canonical_json(obj: Any) -> str:
    """Serialize a Python object into canonical sorted JSON without whitespace.

    Matches RFC 8785 / deterministic canonical JSON rules so that hashing
    produces identical digests across platforms and implementations.
    """
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(by_alias=True)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Grants:
    """Represents capability grants defining allowed tools, hosts, and paths."""

    def __init__(
        self,
        allowed_tools: set[str] | None = None,
        allowed_hosts: set[str] | None = None,
        allowed_paths: set[str] | None = None,
    ) -> None:
        self.allowed_tools = allowed_tools
        self.allowed_hosts = allowed_hosts
        self.allowed_paths = allowed_paths

    def covers(self, m: ToolManifest, input_args: dict[str, Any]) -> bool:
        """Verify whether tool and its target resources are covered by grants."""
        if self.allowed_tools is not None and m.id not in self.allowed_tools:
            return False

        if self.allowed_hosts is not None and isinstance(input_args.get("url"), str):
            try:
                parsed = urllib.parse.urlparse(input_args["url"])
                host = parsed.hostname
                if not host or host not in self.allowed_hosts:
                    return False
            except Exception:
                return False

        return True


def render_card(
    m: ToolManifest,
    args: Value[dict[str, Any]],
    s: SessionTaint,
) -> ActionCard:
    """Deterministically render a human-in-the-loop ActionCard from real tool arguments."""
    approval_id = str(uuid.uuid4())
    nonce = secrets.token_hex(16)
    expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    args_sha256 = hashlib.sha256(canonical_json(args.v).encode("utf-8")).hexdigest()

    title = m.id
    exact = canonical_json(args.v)

    if m.id == "web_fetch":
        title = "Fetch External Web URL"
        if isinstance(args.v.get("url"), str):
            exact = args.v["url"]
    elif m.id == "browser":
        title = "Browse External Web Page"
        action = str(args.v.get("action", "navigate"))
        url = str(args.v.get("url", ""))
        exact = f"{action} {url}".strip()
    elif m.id == "shell":
        title = "Execute Shell Command"
        if isinstance(args.v.get("command"), str):
            exact = args.v["command"]
    elif m.id == "file_write":
        title = "Write Local File"
        path = str(args.v.get("path", ""))
        content = str(args.v.get("content", ""))
        content_bytes = len(content.encode("utf-8"))
        exact = f"{path} ({content_bytes} bytes)"
    elif m.id == "delegate_agent":
        title = "Delegate to Local Agent"
        agent = str(args.v.get("agent", ""))
        prompt = str(args.v.get("prompt", ""))
        exact = f"{agent} {prompt}".strip()

    reversible = (
        "exec" not in m.effects
        and "write_local" not in m.effects
        and "spend" not in m.effects
    )

    rendered = ActionCardRendered(
        title=title,
        exact=exact,
        effects=list(m.effects),
        taint="contaminated" if s.contaminated else "clean",
        reversible=reversible,
    )

    return ActionCard(
        approval_id=approval_id,
        nonce=nonce,
        expires_at=expires_at,
        tool_id=m.id,
        tool_version=m.version,
        args_sha256=args_sha256,
        rendered=rendered,
    )


def sensitive_params_from_untrusted(
    _m: ToolManifest,
    args: Value[dict[str, Any]],
) -> bool:
    """Detect if sensitive arguments (url, path, command, to) originate from untrusted sources."""
    if args.labels and any(label.integrity == "untrusted" for label in args.labels):
        for sensitive_key in ("url", "to", "path", "command"):
            if sensitive_key in args.v and args.v[sensitive_key]:
                return True
    return False


def decide(
    m: ToolManifest,
    args: Value[dict[str, Any]],
    s: SessionTaint,
    g: Grants | None = None,
) -> PolicyDecision:
    """Evaluate an action against security policies and return a PolicyDecision.

    Rules evaluated in deterministic priority order:
    1. Capability checks (Grants): Tool and host must be permitted.
    2. Secret protection: Secret-labeled data cannot be egressed.
    3. Lethal Trifecta: In contaminated sessions, high-impact effects (egress,
       send_message, spend, exec) strictly require an ActionCard.
    4. Sensitive parameter from untrusted origin: Requires an ActionCard.
    5. Allow.
    """
    grants = g or Grants()

    # 1. Capability: tool + resource must be covered by grants (Progent, monotonic)
    if not grants.covers(m, args.v):
        return PolicyDecision(kind="deny", reason="grant")

    # 2. Secret protection: secret-labeled data cannot be egressed (FIDES)
    if "egress" in m.effects and any(label.secrecy == "secret" for label in args.labels):
        return PolicyDecision(kind="deny", reason="secret-egress")

    # 3. Lethal Trifecta: contaminated session with egress/send_message/spend/exec -> ALWAYS requires card
    if s.contaminated and any(
        effect in ("egress", "send_message", "spend", "exec") for effect in m.effects
    ):
        return PolicyDecision(kind="card", card=render_card(m, args, s))

    # 4. Sensitive parameter from untrusted source -> requires card
    if sensitive_params_from_untrusted(m, args):
        return PolicyDecision(kind="card", card=render_card(m, args, s))

    return PolicyDecision(kind="allow")
