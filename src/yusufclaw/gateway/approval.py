"""ApprovalProvider interface and ActionCard formatting for human-in-the-loop approvals.

Defines the protocol contract for registering, validating, and consuming interactive
action cards, specifically used by the Telegram gateway inline keyboard approvals.
The concrete implementation is owned by the security/tools subsystem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ActionCardRendered(BaseModel):
    """Deterministic, unalterable human-facing action card summary."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    exact: str
    effects: list[str] = Field(default_factory=list)
    taint: Literal["clean", "contaminated"] = "clean"
    reversible: bool = False


class ActionCard(BaseModel):
    """Verifiable Action Card data structure (YC-05)."""

    model_config = ConfigDict(populate_by_name=True)

    approval_id: str = Field(alias="approvalId")
    nonce: str
    expires_at: str | None = Field(default=None, alias="expiresAt")
    tool_id: str = Field(default="", alias="toolId")
    tool_version: str = Field(default="1.0.0", alias="toolVersion")
    args_sha256: str = Field(default="", alias="argsSha256")
    rendered: ActionCardRendered

    @property
    def approvalId(self) -> str:
        return self.approval_id

    @property
    def expiresAt(self) -> str | None:
        return self.expires_at

    @property
    def toolId(self) -> str:
        return self.tool_id

    @property
    def toolVersion(self) -> str:
        return self.tool_version

    @property
    def argsSha256(self) -> str:
        return self.args_sha256


class ApprovalResult(BaseModel):
    """Result of attempting to consume an approval card."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    reason: str | None = None
    card: ActionCard | None = None


class ApprovalRecord(BaseModel):
    """Stored record of an ActionCard in an approval provider."""

    model_config = ConfigDict(populate_by_name=True)

    card: ActionCard
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    input_args: dict[str, Any] | None = None


@runtime_checkable
class ApprovalProvider(Protocol):
    """Protocol for human approval verification and action card lifecycle.

    Gateway channels interact exclusively with this protocol to request approvals,
    render verifiable inline cards, and consume or deny actions.
    The real implementation with taint tracking and guard rules is in security/tools.
    """

    def register(self, card: ActionCard, input_args: dict[str, Any] | None = None) -> None:
        """Register an ActionCard with optional arguments for verification."""
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


def format_telegram_card_message(
    card: ActionCard | dict[str, Any], agent_claim: str | None = None
) -> str:
    """Format a deterministic ActionCard into human-readable Telegram alert text."""
    if isinstance(card, dict):
        c = ActionCard.model_validate(card)
    else:
        c = card

    taint_badge = (
        "🔴 KİRLİ (Güvenilmeyen veri görüldü)"
        if c.rendered.taint == "contaminated"
        else "🟢 TEMİZ"
    )
    effects_str = ", ".join(c.rendered.effects) if c.rendered.effects else "yok"
    reversible_str = "Evet" if c.rendered.reversible else "Hayır (Geri alınamaz)"

    msg = (
        f"⚠️ [ONAY GEREKİYOR: {c.rendered.title}]\n\n"
        f"• Eylem (Kesin): {c.rendered.exact}\n"
        f"• Etkiler: {effects_str}\n"
        f"• Oturum Durumu: {taint_badge}\n"
        f"• Geri Alınabilir: {reversible_str}\n"
        f"• Kart No: {c.approval_id[:8]}\n"
    )

    if agent_claim and agent_claim.strip():
        msg += f'\n[Ajanın İddiası (Güvenilmeyen Açıklama)]:\n"{agent_claim.strip()}"\n'

    return msg


class InMemoryApprovalProvider:
    """Thread-safe in-memory reference implementation of ApprovalProvider with YC-05 protections."""

    def __init__(self) -> None:
        self.cards: dict[str, ActionCard] = {}
        self.records: dict[str, ApprovalRecord] = {}
        self.expected_args: dict[str, dict[str, Any]] = {}

    def register(self, card: ActionCard, input_args: dict[str, Any] | None = None) -> None:
        """Register an ActionCard with its expected arguments."""
        self.cards[card.approval_id] = card
        self.records[card.approval_id] = ApprovalRecord(
            card=card,
            status="pending",
            input_args=input_args,
        )
        if input_args is not None:
            self.expected_args[card.approval_id] = input_args

    def consume(
        self,
        approval_id: str,
        nonce: str,
        actual_args: dict[str, Any] | None = None,
    ) -> ApprovalResult:
        """Consume an approved action card with nonce, replay, and argument tampering protection."""
        record = self.records.get(approval_id)
        if not record:
            return ApprovalResult(success=False, reason="Action card not found")

        if record.status in ("approved", "consumed"):
            return ApprovalResult(
                success=False, reason="Replay detected: action card already consumed"
            )

        if record.status == "denied":
            return ApprovalResult(success=False, reason="Action card was denied")

        if record.card.expires_at:
            try:
                expires_dt = datetime.fromisoformat(record.card.expires_at)
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=UTC)
                if expires_dt < datetime.now(UTC):
                    record.status = "expired"
                    return ApprovalResult(
                        success=False, reason="Action card has expired"
                    )
            except Exception:
                pass

        if record.card.nonce != nonce:
            return ApprovalResult(
                success=False, reason="Invalid nonce: nonce mismatch"
            )

        expected = self.expected_args.get(approval_id)
        if expected is not None and actual_args is not None:
            if actual_args != expected:
                return ApprovalResult(
                    success=False,
                    reason="Argument tampering detected: args hash mismatch",
                )

        record.status = "approved"
        return ApprovalResult(success=True, card=record.card)

    def deny(self, approval_id: str, nonce: str) -> None:
        """Deny approval for the specified card and nonce."""
        record = self.records.get(approval_id)
        if not record:
            return
        if record.card.nonce != nonce:
            return
        record.status = "denied"

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Retrieve stored approval record by ID."""
        return self.records.get(approval_id)


def build_telegram_card_keyboard(card: ActionCard | dict[str, Any]) -> dict[str, Any]:
    """Build Telegram inline keyboard containing verifiable callback queries."""
    if isinstance(card, dict):
        c = ActionCard.model_validate(card)
    else:
        c = card

    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Bir kez onayla",
                    "callback_data": f"approve:{c.approval_id}:{c.nonce}",
                },
                {
                    "text": "❌ Reddet",
                    "callback_data": f"deny:{c.approval_id}:{c.nonce}",
                },
            ],
            [
                {
                    "text": "ℹ️ Ayrıntı",
                    "callback_data": f"details:{c.approval_id}:{c.nonce}",
                }
            ],
        ]
    }


__all__ = [
    "ActionCard",
    "ActionCardRendered",
    "ApprovalProvider",
    "ApprovalRecord",
    "ApprovalResult",
    "InMemoryApprovalProvider",
    "build_telegram_card_keyboard",
    "format_telegram_card_message",
]
