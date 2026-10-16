"""OpenAI-compatible STT (whisper-1) and TTS (tts-1) implementations."""

from __future__ import annotations

import io
from typing import Any

from openai import AsyncOpenAI

from yusufclaw.voice.types import (
    AudioResult,
    SynthesisError,
    TranscriptionError,
    TranscriptionResult,
)


class OpenAISTT:
    """OpenAI Audio Transcriptions client using whisper-1."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "whisper-1",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or AsyncOpenAI(
            api_key=api_key or "placeholder",
            base_url=base_url or "https://api.openai.com/v1",
        )

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using OpenAI Whisper API."""
        try:
            # Prepare in-memory audio file tuple (filename, file_bytes, mime_type)
            ext = "ogg"
            if mime_type:
                if "mp3" in mime_type or "mpeg" in mime_type:
                    ext = "mp3"
                elif "wav" in mime_type:
                    ext = "wav"
                elif "m4a" in mime_type or "mp4" in mime_type:
                    ext = "m4a"
            filename = f"audio.{ext}"
            file_obj = io.BytesIO(audio)
            file_tuple = (filename, file_obj, mime_type or "audio/ogg")

            kwargs: dict[str, Any] = {
                "model": self.model,
                "file": file_tuple,
            }
            if language:
                kwargs["language"] = language

            resp = await self.client.audio.transcriptions.create(**kwargs)
            text = getattr(resp, "text", "")
            return TranscriptionResult(text=text, language=language)
        except Exception as exc:
            raise TranscriptionError(f"OpenAI transcription failed: {exc}") from exc


class OpenAITTS:
    """OpenAI Text-to-Speech client using tts-1 or tts-1-hd."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "tts-1",
        voice: str = "alloy",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.voice = voice
        self.client = client or AsyncOpenAI(
            api_key=api_key or "placeholder",
            base_url=base_url or "https://api.openai.com/v1",
        )

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> AudioResult:
        """Synthesize text into speech audio bytes using OpenAI TTS API."""
        try:
            response = await self.client.audio.speech.create(
                model=self.model,
                voice=voice_id or self.voice,
                input=text,
                response_format=output_format,  # type: ignore[arg-type]
            )
            audio_bytes: bytes
            if hasattr(response, "aread"):
                audio_bytes = await response.aread()
            elif hasattr(response, "content"):
                audio_bytes = response.content
            elif hasattr(response, "read"):
                audio_bytes = response.read()
            else:
                audio_bytes = b""

            return AudioResult(
                audioBytes=audio_bytes,
                mimeType=f"audio/{output_format}",
            )
        except Exception as exc:
            raise SynthesisError(f"OpenAI synthesis failed: {exc}") from exc


__all__ = ["OpenAISTT", "OpenAITTS"]
