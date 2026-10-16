"""Gateway subpackage for YusufClaw.

Implements multi-channel adapters connecting users to the core agent:
- MessageBus: Async event bus with channel allowlist and session key formatting.
- CLI: Local terminal interactive sessions and single turns.
- Web: FastAPI gateway streaming AG-UI events over SSE plus minimal web UI.
- Telegram: Bot API adapter with allowlists, chunking, and ActionCard approvals.
- ApprovalProvider: Contract for verifying interactive approvals (YC-05).
- Config: Pydantic-based configuration validation matching TypeScript spec.
"""

from yusufclaw.gateway.approval import (
    ActionCard,
    ActionCardRendered,
    ApprovalProvider,
    ApprovalRecord,
    ApprovalResult,
    build_telegram_card_keyboard,
    format_telegram_card_message,
)
from yusufclaw.gateway.bus import (
    ChannelType,
    IncomingMessage,
    MessageBus,
    UnauthorizedUserError,
)
from yusufclaw.gateway.channels import ChannelRegistry, setup_channels
from yusufclaw.gateway.cli import cli_app, run_cli
from yusufclaw.gateway.config import GatewayConfig, load_config, loadConfig
from yusufclaw.gateway.main import async_main, main
from yusufclaw.gateway.telegram import (
    TelegramAdapter,
    TelegramApi,
    TelegramApiError,
    TelegramCallbackQuery,
    TelegramChat,
    TelegramFrom,
    TelegramMessage,
    TelegramTransport,
    TelegramUpdate,
    TelegramVoice,
    split_telegram_text,
)
from yusufclaw.gateway.whatsapp import (
    WhatsAppAdapter,
    WhatsAppApi,
    create_whatsapp_router,
    normalize_phone_number,
    split_whatsapp_text,
)
from yusufclaw.gateway.web import (
    ChatMessageInput,
    HealthResponse,
    InboxItem,
    RoutineTarget,
    ServerAddress,
    ServerWrapper,
    WebAdapter,
    WebAdapterOptions,
    app,
    create_app,
    format_sse_event,
)
from yusufclaw.gateway.web_page import (
    CHAT_PAGE,
    CHAT_SCRIPT,
    CHAT_STYLE,
    chatPage,
    chatScript,
    chatStyle,
)

__all__ = [
    "CHAT_PAGE",
    "CHAT_SCRIPT",
    "CHAT_STYLE",
    "ActionCard",
    "ActionCardRendered",
    "ApprovalProvider",
    "ApprovalRecord",
    "ApprovalResult",
    "ChannelRegistry",
    "ChannelType",
    "ChatMessageInput",
    "GatewayConfig",
    "HealthResponse",
    "InboxItem",
    "IncomingMessage",
    "MessageBus",
    "RoutineTarget",
    "ServerAddress",
    "ServerWrapper",
    "TelegramAdapter",
    "TelegramApi",
    "TelegramApiError",
    "TelegramCallbackQuery",
    "TelegramChat",
    "TelegramFrom",
    "TelegramMessage",
    "TelegramTransport",
    "TelegramUpdate",
    "TelegramVoice",
    "UnauthorizedUserError",
    "WhatsAppAdapter",
    "WhatsAppApi",
    "WebAdapter",
    "WebAdapterOptions",
    "app",
    "async_main",
    "build_telegram_card_keyboard",
    "chatPage",
    "chatScript",
    "chatStyle",
    "cli_app",
    "create_app",
    "create_whatsapp_router",
    "format_sse_event",
    "format_telegram_card_message",
    "loadConfig",
    "load_config",
    "main",
    "normalize_phone_number",
    "run_cli",
    "setup_channels",
    "split_telegram_text",
    "split_whatsapp_text",
]
