"""Deterministic, offline fake STT and TTS implementations for unit tests and local runs."""

from __future__ import annotations

from typing import Any

from yusufclaw.voice.types import (
    AudioResult,
    SynthesisError,
    TranscriptionError,
    TranscriptionResult,
)


class FakeSTT:
    """Fake SpeechToText provider for offline testing and deterministic transcriptions."""

    def __init__(
        self,
        default_text: str | None = None,
        *,
        should_fail: bool = False,
    ) -> None:
        self.default_text = default_text
        self.should_fail = should_fail
        self.recorded_calls: list[dict[str, Any]] = []

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        if self.should_fail:
            raise TranscriptionError("Simulated STT failure")

        self.recorded_calls.append(
            {
                "audio_len": len(audio),
                "mime_type": mime_type,
                "language": language,
            }
        )

        text = self.default_text
        if text is None:
            text = f"Transcribed audio ({len(audio)} bytes)"

        return TranscriptionResult(
            text=text,
            confidence=1.0,
            language=language or "en",
            durationSeconds=float(len(audio)) / 1000.0,
        )


class FakeTTS:
    """Fake TextToSpeech provider for offline testing and deterministic audio synthesis."""

    def __init__(
        self,
        dummy_audio: bytes | None = None,
        *,
        should_fail: bool = False,
    ) -> None:
        self.dummy_audio = dummy_audio
        self.should_fail = should_fail
        self.recorded_calls: list[dict[str, Any]] = []

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> AudioResult:
        if self.should_fail:
            raise SynthesisError("Simulated TTS failure")

        self.recorded_calls.append(
            {
                "text": text,
                "voice_id": voice_id,
                "output_format": output_format,
            }
        )

        audio_bytes = self.dummy_audio
        if audio_bytes is None:
            audio_bytes = b"FAKE_AUDIO:" + text.encode("utf-8")

        return AudioResult(
            audioBytes=audio_bytes,
            mimeType=f"audio/{output_format}",
            durationSeconds=1.5,
        )


__all__ = ["FakeSTT", "FakeTTS"]
