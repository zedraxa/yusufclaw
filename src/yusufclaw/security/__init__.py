"""Security subpackage for YusufClaw.

Implements security policies, taint tracking, and approval mechanisms:
- Deterministic policy engine: pre-execution validation of actions.
- Taint tracking: tracks data provenance across agent turns.
- Action cards: human-in-the-loop approval cards generated from concrete actions.
- Access control: SSRF prevention, path traversal defense, and secret-file denial.
- Allowlist verification: channel and user authorization checks.
"""

from yusufclaw.security.cards import ActionCardStore
from yusufclaw.security.engine import (
    Grants,
    canonical_json,
    decide,
    render_card,
    sensitive_params_from_untrusted,
)
from yusufclaw.security.models import (
    ActionCard,
    ActionCardRendered,
    CardRecord,
    CardStatus,
    ConsumeResult,
    Integrity,
    Label,
    PolicyDecision,
    Secrecy,
    SessionTaint,
    ToolEffect,
    ToolManifest,
    ToolResources,
    ToolSource,
    Value,
)
from yusufclaw.security.telegram import (
    build_telegram_card_keyboard,
    format_telegram_card_message,
    handle_telegram_callback_data,
)

__all__ = [
    "ActionCard",
    "ActionCardRendered",
    "ActionCardStore",
    "CardRecord",
    "CardStatus",
    "ConsumeResult",
    "Grants",
    "Integrity",
    "Label",
    "PolicyDecision",
    "Secrecy",
    "SessionTaint",
    "ToolEffect",
    "ToolManifest",
    "ToolResources",
    "ToolSource",
    "Value",
    "build_telegram_card_keyboard",
    "canonical_json",
    "decide",
    "format_telegram_card_message",
    "handle_telegram_callback_data",
    "render_card",
    "sensitive_params_from_untrusted",
]
