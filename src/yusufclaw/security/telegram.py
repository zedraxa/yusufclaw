"""Telegram inline approval payloads and callback validation with consent integrity.

Implements YC-05:
- Deterministic ActionCard text formatting ensuring user consent integrity (VAC).
- Inline keyboard callback payloads containing approval ID and nonce.
- Callback query processing with replay and argument tampering protection.
"""

from __future__ import annotations

from typing import Any

from yusufclaw.security.cards import ActionCardStore
from yusufclaw.security.models import ActionCard


def format_telegram_card_message(card: ActionCard, agent_claim: str | None = None) -> str:
    """Format an ActionCard into a deterministic, prompt-injection-safe Telegram message."""
    taint_badge = (
        "🔴 KİRLİ (Güvenilmeyen veri görüldü)"
        if card.rendered.taint == "contaminated"
        else "🟢 TEMİZ"
    )
    effects_str = ", ".join(card.rendered.effects) if card.rendered.effects else "yok"
    reversible_str = "Evet" if card.rendered.reversible else "Hayır (Geri alınamaz)"

    msg = (
        f"⚠️ [ONAY GEREKİYOR: {card.rendered.title}]\n\n"
        f"• Eylem (Kesin): {card.rendered.exact}\n"
        f"• Etkiler: {effects_str}\n"
        f"• Oturum Durumu: {taint_badge}\n"
        f"• Geri Alınabilir: {reversible_str}\n"
        f"• Kart No: {card.approval_id[:8]}\n"
    )

    if agent_claim and agent_claim.strip():
        msg += f'\n[Ajanın İddiası (Güvenilmeyen Açıklama)]:\n"{agent_claim.strip()}"\n'

    return msg


def build_telegram_card_keyboard(card: ActionCard) -> dict[str, Any]:
    """Construct standard Telegram inline keyboard markup with callback data."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Bir kez onayla",
                    "callback_data": f"approve:{card.approval_id}:{card.nonce}",
                },
                {
                    "text": "❌ Reddet",
                    "callback_data": f"deny:{card.approval_id}:{card.nonce}",
                },
            ],
            [
                {
                    "text": "ℹ️ Ayrıntı",
                    "callback_data": f"details:{card.approval_id}:{card.nonce}",
                }
            ],
        ]
    }


def handle_telegram_callback_data(
    data: str,
    user_id: str,
    allowed_users: set[str],
    card_store: ActionCardStore,
    actual_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and process an incoming Telegram inline button callback query."""
    if user_id not in allowed_users:
        return {
            "status": "unauthorized",
            "message": "Yetkisiz kullanıcı",
            "approved": False,
        }

    parts = data.split(":")
    if len(parts) != 3:
        return {
            "status": "error",
            "message": "Geçersiz onay isteği",
            "approved": False,
        }

    action, approval_id, nonce = parts

    if action == "approve":
        result = card_store.consume(approval_id, nonce, actual_args=actual_args)
        if not result.success:
            return {
                "status": "rejected",
                "message": f"Onay reddedildi: {result.reason}",
                "approved": False,
            }
        return {
            "status": "approved",
            "message": "✅ Eylem onaylandı ve çalıştırılıyor",
            "approved": True,
        }

    if action == "deny":
        card_store.deny(approval_id, nonce)
        return {
            "status": "denied",
            "message": "❌ Eylem reddedildi",
            "approved": False,
        }

    if action == "details":
        record = card_store.get(approval_id)
        if record:
            effects_str = ", ".join(record.card.rendered.effects)
            sha_prefix = record.card.args_sha256[:16]
            info = f"Etkiler: {effects_str}\nArgs SHA: {sha_prefix}..."
        else:
            info = "Kart bulunamadı"
        return {
            "status": "details",
            "message": info,
            "approved": False,
        }

    return {
        "status": "error",
        "message": "Bilinmeyen eylem",
        "approved": False,
    }
