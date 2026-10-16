"""Main execution entry point for YusufClaw Gateway subsystem.

Connects configuration, storage/memory, model provider, message bus, and channel adapters.
Supports running in cli, web, or telegram modes.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

from yusufclaw.agent import Agent
from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.cli import run_cli
from yusufclaw.gateway.config import GatewayConfig, load_config
from yusufclaw.gateway.telegram import TelegramAdapter, TelegramApi
from yusufclaw.gateway.web import WebAdapter
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.models import get_model_provider
from yusufclaw.models.fake import FakeProvider


async def async_main(args: list[str] | None = None) -> None:
    """Run Gateway in chosen operating mode with signal handling."""
    raw_args = list(sys.argv[1:] if args is None else args)

    if not raw_args or "--help" in raw_args or "-h" in raw_args:
        sys.stdout.write(
            "Usage: yusufclaw cli [--message TEXT] [--session NAME]\n"
            "       yusufclaw telegram\n"
            "       yusufclaw web\n"
            "Configure .env using .env.example.\n"
        )
        sys.stdout.flush()
        return

    mode = raw_args[0]
    extra_args = raw_args[1:]

    if mode not in ("cli", "telegram", "web", "routines"):
        raise ValueError("Choose cli, telegram, web, or routines")

    config: GatewayConfig = load_config()

    if mode == "telegram" and (
        not config.TELEGRAM_BOT_TOKEN or not config.allowed_ids
    ):
        raise ValueError("Telegram requires a token and allowed user IDs")
    if mode == "web" and not config.WEB_AUTH_TOKEN:
        raise ValueError("WEB_AUTH_TOKEN is required")
    if mode == "routines" and not config.ROUTINES_CONFIG:
        raise ValueError("ROUTINES_CONFIG is required")

    # Wire Model Provider
    if config.MODEL_PROVIDER == "fake":
        provider: Any = FakeProvider()
    else:
        from yusufclaw.config import Settings

        settings_dict = config.model_dump()
        settings = Settings(**{k.lower(): v for k, v in settings_dict.items()})
        provider = get_model_provider(settings)

    # Wire SQLite Memory
    memory = SqliteMemory(config.MEMORY_PATH)

    # Wire Core Agent
    agent = Agent(
        provider=provider,
        memory=memory,
        max_steps=config.MAX_AGENT_STEPS,
    )

    # Wire Message Bus
    bus = MessageBus(
        agent,
        cli={config.CLI_USER_ID},
        telegram=set(config.allowed_ids),
        web={config.WEB_USER_ID},
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_signal() -> None:
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except (NotImplementedError, RuntimeError):
            # Windows or non-main thread fallback
            pass

    web_adapter: WebAdapter | None = None
    try:
        if mode == "cli":
            await run_cli(
                bus=bus,
                user_id=config.CLI_USER_ID,
                args=extra_args,
                signal=shutdown_event,
            )
        elif mode == "web":
            web_adapter = WebAdapter(
                bus=bus,
                memory=memory,
                options={
                    "token": config.WEB_AUTH_TOKEN,
                    "user_id": config.WEB_USER_ID,
                    "host": config.WEB_HOST,
                    "port": config.WEB_PORT,
                },
            )
            await web_adapter.listen()
            sys.stdout.write(
                f"Web chat listening on http://{config.WEB_HOST}:{config.WEB_PORT}\n"
            )
            sys.stdout.flush()
            await shutdown_event.wait()
        elif mode == "telegram":
            assert config.TELEGRAM_BOT_TOKEN is not None
            telegram_api = TelegramApi(config.TELEGRAM_BOT_TOKEN)
            telegram_adapter = TelegramAdapter(
                bus=bus,
                memory=memory,
                api=telegram_api,
                allowed=set(config.allowed_ids),
                token=config.TELEGRAM_BOT_TOKEN,
            )
            await telegram_adapter.run(signal=shutdown_event)
        elif mode == "routines":
            # Routines are scheduled with skills later
            sys.stdout.write("Routines runner will be loaded with skills subsystem.\n")
            sys.stdout.flush()
    finally:
        shutdown_event.set()
        if web_adapter is not None:
            await web_adapter.close()
        if hasattr(memory, "close"):
            memory.close()


def main() -> None:
    """Synchronous console script entrypoint."""
    try:
        asyncio.run(async_main())
    except Exception:
        sys.stderr.write(
            "YusufClaw could not complete the request. Check command arguments, "
            ".env, credentials, and provider connectivity.\n"
        )
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()

__all__ = ["async_main", "main"]
