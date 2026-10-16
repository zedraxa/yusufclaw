# Python Gateway Channels: WhatsApp Business Cloud API and Agnostic Voice Layer

`yusufclaw.gateway.whatsapp`, `yusufclaw.voice`, and `yusufclaw.gateway.channels` provide multi-channel inbound/outbound communication adapters mirroring the Telegram adapter design (`yusufclaw.gateway.telegram`), as specified in Architecture v2 Layer 1 & P2, UX-SPEC §2 & §6, and SPEC.md.

---

## 1. Executive Summary & Architecture

YusufClaw operates as an always-on personal agent serving a single human owner. Communication surfaces route through a unified, strictly-isolated `MessageBus` that enforces:
1. **Per-Channel Allowlist Verification**: Senders not in configured allowlists (Telegram user IDs, WhatsApp phone numbers) are rejected immediately; unlisted updates never reach the core agent or consume token budgets.
2. **Channel-Bound Ephemeral Sessions**: Session IDs are deterministically formatted as JSON arrays `[channel, userId, conversationId]` ensuring strict conversation isolation across surfaces.
3. **Cross-Surface Parity for Human-in-the-Loop Approvals (YC-05)**: Consequential actions render tamper-proof Action Cards with interactive reply controls (`approve:<id>:<nonce>`, `deny:<id>:<nonce>`, `details:<id>:<nonce>`). Replay, expired nonces, and argument tampering are rejected fail-closed.
4. **Provider-Agnostic Speech Subsystem**: Inbound voice notes (Telegram OGG Opus, WhatsApp OGG/audio) are transcribed to text before dispatching to the MessageBus; replies can optionally be synthesized to audio.

```
+-----------------------------------------------------------------------------------+
|                                 Human User (Owner)                                |
+-----------------------------------------------------------------------------------+
        |                                                   |
 (Mobile / Instant)                                  (Mobile / Instant)
        v                                                   v
+-----------------------------+                     +-------------------------------+
|    Telegram Bot API         |                     |   WhatsApp Cloud API (Meta)   |
| - Polling / Webhook         |                     | - Webhook GET handshake       |
| - InlineKeyboard approvals  |                     | - POST X-Hub-Signature-256    |
| - Audio / voice notes       |                     | - Interactive reply buttons   |
+-----------------------------+                     +-------------------------------+
        |                                                   |
        | Download audio                                    | Download audio
        v                                                   v
+-----------------------------------------------------------------------------------+
|                        Voice / Speech Subsystem (STT & TTS)                       |
|   - SpeechToText (STT): FakeSTT | OpenAISTT | FasterWhisperSTT | AmazonTranscribe |
|   - TextToSpeech (TTS): FakeTTS | OpenAITTS | AmazonPollyTTS                      |
+-----------------------------------------------------------------------------------+
        |                                                   |
        | Transcribed Text                                  | Transcribed Text
        v                                                   v
+-----------------------------------------------------------------------------------+
|                     MessageBus (Multi-Channel Authorization)                      |
|   - Per-channel allowlist (TELEGRAM_ALLOWED_USER_IDS, WHATSAPP_ALLOWED_NUMBERS)   |
|   - Deterministic session key: ["channel", "user_id", "conversation_id"]          |
|   - ApprovalProvider contract (YC-05 ActionCard verification)                     |
+-----------------------------------------------------------------------------------+
                                        |
                                        v
+-----------------------------------------------------------------------------------+
|                                  Core Agent Loop                                  |
+-----------------------------------------------------------------------------------+
```

---

## 2. WhatsApp Business Cloud API Adapter (`gateway/whatsapp.py`)

### 2.1 Webhook Verification Handshake
During initial Meta app setup, Meta issues an HTTP `GET` request to verify endpoint ownership:
- Parameters: `hub.mode`, `hub.verify_token`, `hub.challenge`.
- Handshake logic:
  ```python
  def verify_webhook(hub_mode: str | None, hub_verify_token: str | None, hub_challenge: str | None) -> str | None:
      if hub_mode == "subscribe" and hub_verify_token == configured_verify_token:
          return hub_challenge or ""
      return None
  ```
- Endpoint returns raw HTTP 200 with the `hub.challenge` string on success, or HTTP 403 Forbidden on failure.

### 2.2 X-Hub-Signature-256 HMAC Authentication
All incoming webhook `POST` events carry the signature header `X-Hub-Signature-256: sha256=<hex_digest>`:
- Computed over the raw request payload bytes using the Meta App Secret (`WHATSAPP_APP_SECRET`).
- Verified using constant-time string comparison (`hmac.compare_digest`) to prevent timing side-channel attacks.
- If signature check fails, request is rejected with HTTP 403 / `PermissionError`.

### 2.3 Sender Allowlist Authorization
- Senders are normalized by stripping non-digit characters (`normalize_phone_number("+90 555 123-4567") -> "905551234567"`).
- Only numbers listed in `WHATSAPP_ALLOWED_NUMBERS` are processed.
- Messages from unlisted senders are dropped silently; no event is sent to the MessageBus and no model turns are run.

### 2.4 Interactive Action Card Approvals (YC-05 Parity)
When the core agent requests human approval for a high-risk tool call (e.g. file deletion, external network call, shell command):
1. The adapter formats the action card using `format_telegram_card_message(action_card)`.
2. Sends an interactive button message to WhatsApp with up to 3 buttons:
   - Button 1: ID `approve:<approval_id>:<nonce>`, Title `"✅ Onayla"`
   - Button 2: ID `deny:<approval_id>:<nonce>`, Title `"❌ Reddet"`
   - Button 3: ID `details:<approval_id>:<nonce>`, Title `"ℹ️ Ayrıntı"`
3. A 300-second (5-minute) timeout timer starts.
4. When the user taps a button, a message with `type: "interactive"` arrives:
   - `approve`: Calls `ApprovalProvider.consume(...)`. Verifies nonce, arguments, and replay. If valid, confirms execution and unblocks agent loop.
   - `deny`: Calls `ApprovalProvider.deny(...)`, unblocks agent loop with `False`.
   - `details`: Returns exact effects and SHA-256 argument prefix without consuming the card.
   - Replay attempt: Rejected with `"Replay detected"` alert.

### 2.5 Text Chunk Splitting
WhatsApp enforces a 4096-character limit on text messages. The adapter provides `split_whatsapp_text(text, max_chunk_size=4000)` which chunks long responses along character boundaries without corrupting multi-byte UTF-8 or emoji sequences.

### 2.6 FastAPI Router Helper
`create_whatsapp_router(adapter, prefix="/webhook/whatsapp")` constructs a pluggable FastAPI `APIRouter` with preconfigured `GET` verification and `POST` webhook endpoints.

---

## 3. Agnostic Speech/Voice Layer (`yusufclaw.voice`)

The voice subsystem provides abstract protocols and swappable backends for Speech-to-Text (STT) and Text-to-Speech (TTS).

### 3.1 Protocols and Models (`voice/types.py`)

```python
@runtime_checkable
class SpeechToText(Protocol):
    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult: ...

@runtime_checkable
class TextToSpeech(Protocol):
    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> AudioResult: ...
```

### 3.2 Supported Backends

| Backend | Protocol | Description | Configuration Keys |
|---|---|---|---|
| `FakeSTT` | STT | Offline, deterministic fake recording calls and returning predictable text. | `VOICE_STT_PROVIDER=fake` |
| `FakeTTS` | TTS | Offline, deterministic fake returning dummy audio bytes with format MIME. | `VOICE_TTS_PROVIDER=fake` |
| `OpenAISTT` | STT | Cloud Whisper API (`whisper-1`) using async `openai` client. | `VOICE_STT_PROVIDER=openai`, `OPENAI_STT_MODEL` |
| `OpenAITTS` | TTS | Cloud Speech API (`tts-1`) with selectable voices (`alloy`, `echo`, `shimmer`). | `VOICE_TTS_PROVIDER=openai`, `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE` |
| `FasterWhisperSTT` | STT | Local CPU/GPU whisper model running asynchronously via worker thread. | `VOICE_STT_PROVIDER=whisper`, `WHISPER_MODEL_SIZE` |
| `AmazonTranscribeSTT` | STT | AWS cloud speech-to-text transcription via `boto3`. | `VOICE_STT_PROVIDER=amazon` |
| `AmazonPollyTTS` | TTS | AWS cloud neural speech synthesis via `boto3`. | `VOICE_TTS_PROVIDER=amazon`, `POLLY_VOICE_ID` |

### 3.3 Factory Resolution (`voice/factory.py`)
- `get_stt_provider("fake" | "openai" | "whisper" | "amazon", **kwargs) -> SpeechToText`
- `get_tts_provider("fake" | "openai" | "amazon", **kwargs) -> TextToSpeech`

---

## 4. Telegram Voice Hook (`gateway/telegram.py`)

The Telegram adapter includes minimal non-breaking hooks:
1. `TelegramMessage` supports optional `voice: TelegramVoice` and `audio: TelegramVoice`.
2. When an update arrives with an audio attachment and no text:
   - If `stt` is configured: Calls `getFile` on Telegram Bot API, downloads file bytes via `TelegramApi.download_file`, transcribes via `stt.transcribe(...)`, and dispatches the transcribed text.
   - If `stt` is not configured: Safely ignores the voice message.
3. Outbound reply: If `reply_as_voice` is enabled and `tts` is configured, synthesizes speech and sends via `sendVoice` with caption.

---

## 5. Channel Wiring and Registry (`gateway/channels.py`)

Channels remain disabled unless all credentials and allowlists are provided:
- `setup_channels(config, bus, memory, approval_provider)`:
  - If Telegram configured (`TELEGRAM_BOT_TOKEN`, `allowed_ids`): Wires and registers `telegram`.
  - If WhatsApp configured (`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `allowed_whatsapp_numbers`): Wires and registers `whatsapp`.
  - If voice enabled (`VOICE_ENABLED=True`): Instantiates STT/TTS providers and attaches them to channel adapters.
  - Returns `ChannelRegistry`.

---

## 6. Configuration Reference (`.env.example`)

```bash
# WhatsApp Business Cloud API (disabled unless configured)
WHATSAPP_ACCESS_TOKEN=replace_with_meta_system_user_token
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_APP_SECRET=replace_with_meta_app_secret
WHATSAPP_VERIFY_TOKEN=replace_with_webhook_verify_token
WHATSAPP_ALLOWED_NUMBERS=15550234567,905551234567

# Voice Subsystem (disabled unless configured)
VOICE_ENABLED=false
VOICE_STT_PROVIDER=fake
VOICE_TTS_PROVIDER=fake
VOICE_REPLY_AS_AUDIO=false
OPENAI_STT_MODEL=whisper-1
OPENAI_TTS_MODEL=tts-1
OPENAI_TTS_VOICE=alloy
WHISPER_MODEL_SIZE=base
POLLY_VOICE_ID=Joanna
```

---

## 7. Verification and Offline Testing

All tests run completely offline without external network calls or cloud infrastructure:
```bash
uv run pytest -q tests/python/test_voice.py tests/python/test_whatsapp_gateway.py tests/python/test_channels_wiring.py
uv run ruff check .
uv run mypy src
```
