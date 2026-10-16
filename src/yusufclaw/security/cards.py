"""ActionCard store and atomic consumption lifecycle.

Implements human-in-the-loop approval management with:
- Cryptographic 16-byte nonce binding.
- Time-to-live (TTL) expiration enforcement.
- Canonical JSON SHA-256 argument verification to prevent argument tampering (VAC).
- Single-use atomic consumption to prevent replay attacks (LITL).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from yusufclaw.security.engine import canonical_json, render_card
from yusufclaw.security.models import (
    ActionCard,
    CardRecord,
    ConsumeResult,
    SessionTaint,
    ToolManifest,
    Value,
)


class ActionCardStore:
    """Thread-safe in-memory store for ActionCards and atomic approval operations."""

    def __init__(self) -> None:
        self._records: dict[str, CardRecord] = {}

    def register(
        self,
        card: ActionCard,
        expected_args: dict[str, Any] | None = None,
    ) -> None:
        """Register an existing ActionCard in the store."""
        self._records[card.approval_id] = CardRecord(
            card=card,
            status="pending",
            expected_args=expected_args,
        )

    def create(
        self,
        m: ToolManifest,
        args: Value[dict[str, Any]],
        s: SessionTaint,
        ttl_seconds: float = 300.0,
    ) -> ActionCard:
        """Render and register a new ActionCard with a specified TTL."""
        card = render_card(m, args, s)
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        card.expires_at = expires.isoformat()
        self.register(card, expected_args=args.v)
        return card

    def get(self, approval_id: str) -> CardRecord | None:
        """Retrieve a card record by its approval ID."""
        return self._records.get(approval_id)

    def consume(
        self,
        approval_id: str,
        nonce: str,
        actual_args: dict[str, Any] | None = None,
    ) -> ConsumeResult:
        """Atomically consume an ActionCard, validating nonce and argument integrity."""
        record = self._records.get(approval_id)
        if not record:
            return ConsumeResult(success=False, reason="Action card not found")

        if record.status == "consumed":
            return ConsumeResult(
                success=False,
                reason="Replay detected: action card already consumed",
            )

        if record.status == "denied":
            return ConsumeResult(
                success=False,
                reason="Action card was denied",
            )

        try:
            expires_dt = datetime.fromisoformat(record.card.expires_at)
            # Ensure timezone-aware UTC comparison
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=UTC)
            if expires_dt < datetime.now(UTC):
                record.status = "expired"
                return ConsumeResult(
                    success=False,
                    reason="Action card has expired",
                )
        except Exception:
            pass

        if record.card.nonce != nonce:
            return ConsumeResult(
                success=False,
                reason="Invalid nonce: nonce mismatch",
            )

        if actual_args is not None:
            actual_hash = hashlib.sha256(
                canonical_json(actual_args).encode("utf-8")
            ).hexdigest()
            if actual_hash != record.card.args_sha256:
                return ConsumeResult(
                    success=False,
                    reason="Argument tampering detected: args hash mismatch",
                )

        record.status = "consumed"
        return ConsumeResult(success=True, card=record.card)

    def deny(self, approval_id: str, nonce: str) -> ConsumeResult:
        """Deny an ActionCard and prevent future consumption."""
        record = self._records.get(approval_id)
        if not record:
            return ConsumeResult(success=False, reason="Action card not found")

        if record.status == "consumed":
            return ConsumeResult(
                success=False,
                reason="Replay detected: action card already consumed",
            )

        if record.card.nonce != nonce:
            return ConsumeResult(
                success=False,
                reason="Invalid nonce: nonce mismatch",
            )

        record.status = "denied"
        return ConsumeResult(success=True, card=record.card)

    def clear(self) -> None:
        """Clear all registered action cards."""
        self._records.clear()
