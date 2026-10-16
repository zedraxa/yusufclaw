"""Factory functions to instantiate STT and TTS providers by name and configuration."""

from __future__ import annotations

from typing import Any

from yusufclaw.voice.aws_voice import AmazonPollyTTS, AmazonTranscribeSTT
from yusufclaw.voice.fake import FakeSTT, FakeTTS
from yusufclaw.voice.openai_voice import OpenAISTT, OpenAITTS
from yusufclaw.voice.types import SpeechToText, TextToSpeech
from yusufclaw.voice.whisper_voice import FasterWhisperSTT


def get_stt_provider(provider_type: str = "fake", **kwargs: Any) -> SpeechToText:
    """Return an STT provider instance based on provider name.

    Supported provider types: 'fake', 'openai', 'whisper' / 'faster-whisper', 'amazon' / 'transcribe'.
    """
    normalized = provider_type.lower().strip()
    if normalized == "fake":
        return FakeSTT(**kwargs)
    if normalized == "openai":
        return OpenAISTT(**kwargs)
    if normalized in ("whisper", "faster-whisper", "faster_whisper"):
        return FasterWhisperSTT(**kwargs)
    if normalized in ("amazon", "transcribe", "aws"):
        return AmazonTranscribeSTT(**kwargs)
    raise ValueError(f"Unknown STT provider: '{provider_type}'")


def get_tts_provider(provider_type: str = "fake", **kwargs: Any) -> TextToSpeech:
    """Return a TTS provider instance based on provider name.

    Supported provider types: 'fake', 'openai', 'amazon' / 'polly' / 'aws'.
    """
    normalized = provider_type.lower().strip()
    if normalized == "fake":
        return FakeTTS(**kwargs)
    if normalized == "openai":
        return OpenAITTS(**kwargs)
    if normalized in ("amazon", "polly", "aws"):
        return AmazonPollyTTS(**kwargs)
    raise ValueError(f"Unknown TTS provider: '{provider_type}'")


__all__ = ["get_stt_provider", "get_tts_provider"]
