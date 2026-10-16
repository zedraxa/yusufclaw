"""Type definitions, result models, and protocols for the speech/voice layer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class VoiceError(Exception):
    """Base exception for speech/voice subsystem operations."""


class TranscriptionError(VoiceError):
    """Raised when speech-to-text transcription fails."""


class SynthesisError(VoiceError):
    """Raised when text-to-speech audio synthesis fails."""


class TranscriptionResult(BaseModel):
    """Result of transcribing audio to text."""

    model_config = ConfigDict(extra="ignore")

    text: str
    confidence: float | None = None
    language: str | None = None
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")


class AudioResult(BaseModel):
    """Result of synthesizing text to audio bytes."""

    model_config = ConfigDict(extra="ignore")

    audio_bytes: bytes = Field(alias="audioBytes")
    mime_type: str = Field(default="audio/mp3", alias="mimeType")
    duration_seconds: float | None = Field(default=None, alias="durationSeconds")


@runtime_checkable
class SpeechToText(Protocol):
    """Protocol for transcribing audio notes into text."""

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe raw audio bytes into text."""
        ...


@runtime_checkable
class TextToSpeech(Protocol):
    """Protocol for synthesizing text into speech audio bytes."""

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> AudioResult:
        """Synthesize text into audio bytes."""
        ...


__all__ = [
    "AudioResult",
    "SpeechToText",
    "SynthesisError",
    "TextToSpeech",
    "TranscriptionError",
    "TranscriptionResult",
    "VoiceError",
]
