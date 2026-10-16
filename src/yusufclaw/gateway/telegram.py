"""Telegram gateway adapter for YusufClaw.

Implements the Telegram bot interface:
- Webhook and polling modes for receiving Telegram updates.
- User ID authorization checking against configured allowlists.
- Session-based conversation routing to the core agent loop.
- Interactive inline keyboards for action card approval requests (P0-03).
- Rate limiting and media attachment forwarding.
"""

from __future__ import annotations

from typing import Any

from yusufclaw.security.cards import ActionCardStore
from yusufclaw.security.telegram import (
    build_telegram_card_keyboard,
    format_telegram_card_message,
    handle_telegram_callback_data,
)


class TelegramAdapter:
    """Telegram channel integration with ActionCard approval handling."""

    def __init__(
        self,
        bot_token: str | None = None,
        allowed_users: list[str] | None = None,
        card_store: ActionCardStore | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_users = allowed_users or []
        self.card_store = card_store or ActionCardStore()

    def handle_callback_query(
        self,
        data: str,
        user_id: str,
        actual_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process incoming callback query data with replay and tamper verification."""
        return handle_telegram_callback_data(
            data=data,
            user_id=user_id,
            allowed_users=set(self.allowed_users),
            card_store=self.card_store,
            actual_args=actual_args,
        )

    async def start(self) -> None:
        """Start listening for updates (webhook or polling)."""
        raise NotImplementedError("Telegram adapter logic will be ported in subsequent phases.")

    async def stop(self) -> None:
        """Stop listening for updates."""
        pass


__all__ = [
    "TelegramAdapter",
    "build_telegram_card_keyboard",
    "format_telegram_card_message",
    "handle_telegram_callback_data",
]
