"""Voice subpackage for YusufClaw providing speech-to-text (STT) and text-to-speech (TTS).

Contains provider-agnostic protocols, deterministic fakes for testing, and optional
real backends (OpenAI Whisper / TTS, local faster-whisper, Amazon Transcribe / Polly).
"""

from yusufclaw.voice.aws_voice import AmazonPollyTTS, AmazonTranscribeSTT
from yusufclaw.voice.factory import get_stt_provider, get_tts_provider
from yusufclaw.voice.fake import FakeSTT, FakeTTS
from yusufclaw.voice.openai_voice import OpenAISTT, OpenAITTS
from yusufclaw.voice.types import (
    AudioResult,
    SpeechToText,
    SynthesisError,
    TextToSpeech,
    TranscriptionError,
    TranscriptionResult,
    VoiceError,
)
from yusufclaw.voice.whisper_voice import FasterWhisperSTT

__all__ = [
    "AmazonPollyTTS",
    "AmazonTranscribeSTT",
    "AudioResult",
    "FakeSTT",
    "FakeTTS",
    "FasterWhisperSTT",
    "OpenAISTT",
    "OpenAITTS",
    "SpeechToText",
    "SynthesisError",
    "TextToSpeech",
    "TranscriptionError",
    "TranscriptionResult",
    "VoiceError",
    "get_stt_provider",
    "get_tts_provider",
]
