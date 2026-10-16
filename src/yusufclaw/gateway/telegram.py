"""Telegram gateway adapter for YusufClaw.

Implements the Telegram bot interface:
- Webhook and polling modes for receiving Telegram updates.
- User ID authorization checking against configured allowlists.
- Session-based conversation routing to the core agent loop.
- Interactive inline keyboards for action card approval requests (P0-03).
- Rate limiting and media attachment forwarding.

Note: Logic will be ported from TypeScript packages/gateway/src/telegram.ts.
"""


class TelegramAdapter:
    """Placeholder adapter for Telegram channel integration."""

    def __init__(self, bot_token: str | None = None, allowed_users: list[str] | None = None) -> None:
        self.bot_token = bot_token
        self.allowed_users = allowed_users or []

    async def start(self) -> None:
        """Start listening for updates (webhook or polling)."""
        raise NotImplementedError("Telegram adapter logic will be ported in subsequent phases.")

    async def stop(self) -> None:
        """Stop listening for updates."""
        pass


__all__ = ["TelegramAdapter"]
