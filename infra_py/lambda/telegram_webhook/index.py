"""Telegram Webhook Lambda Handler for YusufClaw personal AI agent."""

import base64
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("yusufclaw.webhook")
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Process incoming API Gateway events from Telegram webhook.

    Responsibilities:
    1. Validate optional secret token header (x-telegram-bot-api-secret-token)
    2. Parse Telegram update event
    3. Log metadata for observability
    4. Respond with 200 OK immediately to satisfy Telegram's webhook timeout contract
    """
    http_method = (event.get("httpMethod") or "").upper()

    # Handle health checks / GET
    if http_method == "GET":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "status": "healthy",
                    "service": "yusufclaw-telegram-webhook",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        }

    # Telegram webhook updates must be POST
    if http_method != "POST":
        return {
            "statusCode": 405,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Method Not Allowed"}),
        }

    # Validate Secret Token if configured in environment
    expected_secret = os.environ.get("TELEGRAM_SECRET_HEADER")
    if expected_secret:
        headers = event.get("headers") or {}
        # Header names can be lowercase or titlecase in API Gateway
        incoming_secret = headers.get("x-telegram-bot-api-secret-token") or headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )
        if incoming_secret != expected_secret:
            logger.warning("Unauthorized webhook invocation: secret header mismatch")
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Forbidden"}),
            }

    raw_body = event.get("body")
    if not raw_body:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Empty request body"}),
        }

    try:
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        update = json.loads(raw_body)
        update_id = update.get("update_id")

        message = update.get("message") or {}
        callback_query = update.get("callback_query") or {}
        callback_message = callback_query.get("message") or {}

        chat_id = message.get("chat", {}).get("id") or callback_message.get("chat", {}).get("id")
        from_user = message.get("from", {}).get("username") or callback_query.get("from", {}).get(
            "username"
        )

        logger.info(
            "Received Telegram Update: update_id=%s, chat_id=%s, from=%s, has_text=%s, is_callback=%s",
            update_id,
            chat_id,
            from_user,
            bool(message.get("text")),
            bool(callback_query),
        )

        # Telegram requires a 200 OK fast response
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True, "update_id": update_id}),
        }
    except Exception as exc:
        logger.error("Failed to parse or process update: %s", exc)
        # Return 200 to prevent Telegram retry loop on unrecoverable bad payloads
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": False, "error": "Malformed payload"}),
        }
