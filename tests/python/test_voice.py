"""Unit tests for voice subsystem: protocols, fakes, providers, and factory."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from yusufclaw.voice import (
    AmazonPollyTTS,
    AmazonTranscribeSTT,
    AudioResult,
    FakeSTT,
    FakeTTS,
    FasterWhisperSTT,
    OpenAISTT,
    OpenAITTS,
    SpeechToText,
    SynthesisError,
    TextToSpeech,
    TranscriptionError,
    TranscriptionResult,
    get_stt_provider,
    get_tts_provider,
)


def test_voice_protocols_runtime_checkable() -> None:
    """Verifies that FakeSTT and FakeTTS implement SpeechToText and TextToSpeech protocols."""
    stt = FakeSTT()
    tts = FakeTTS()

    assert isinstance(stt, SpeechToText)
    assert isinstance(tts, TextToSpeech)


@pytest.mark.asyncio
async def test_fake_stt_behavior() -> None:
    """FakeSTT records audio calls and returns deterministic transcription results."""
    stt = FakeSTT(default_text="Hello from voice note")
    res = await stt.transcribe(b"dummy-audio-bytes-1234", mime_type="audio/ogg", language="tr")

    assert isinstance(res, TranscriptionResult)
    assert res.text == "Hello from voice note"
    assert res.language == "tr"
    assert len(stt.recorded_calls) == 1
    assert stt.recorded_calls[0]["audio_len"] == len(b"dummy-audio-bytes-1234")
    assert stt.recorded_calls[0]["mime_type"] == "audio/ogg"

    # Dynamic fallback when default_text is None
    stt2 = FakeSTT()
    res2 = await stt2.transcribe(b"abc")
    assert "3 bytes" in res2.text

    # Failure simulation
    stt_fail = FakeSTT(should_fail=True)
    with pytest.raises(TranscriptionError):
        await stt_fail.transcribe(b"abc")


@pytest.mark.asyncio
async def test_fake_tts_behavior() -> None:
    """FakeTTS records synthesis calls and returns deterministic audio bytes."""
    tts = FakeTTS(dummy_audio=b"CUSTOM_AUDIO_123")
    res = await tts.synthesize("Test message", voice_id="alloy", output_format="ogg")

    assert isinstance(res, AudioResult)
    assert res.audio_bytes == b"CUSTOM_AUDIO_123"
    assert res.mime_type == "audio/ogg"
    assert len(tts.recorded_calls) == 1
    assert tts.recorded_calls[0]["text"] == "Test message"
    assert tts.recorded_calls[0]["voice_id"] == "alloy"

    # Dynamic fallback when dummy_audio is None
    tts2 = FakeTTS()
    res2 = await tts2.synthesize("Echo this")
    assert res2.audio_bytes == b"FAKE_AUDIO:Echo this"

    # Failure simulation
    tts_fail = FakeTTS(should_fail=True)
    with pytest.raises(SynthesisError):
        await tts_fail.synthesize("Will fail")


@pytest.mark.asyncio
async def test_openai_stt_transcription() -> None:
    """OpenAISTT delegates to AsyncOpenAI audio.transcriptions.create."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "OpenAI transcribed text"
    mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_resp)

    stt = OpenAISTT(model="whisper-1", client=mock_client)
    res = await stt.transcribe(b"fake-ogg-data", mime_type="audio/ogg", language="en")

    assert res.text == "OpenAI transcribed text"
    assert mock_client.audio.transcriptions.create.await_count == 1
    call_kwargs = mock_client.audio.transcriptions.create.await_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["language"] == "en"

    # Error handling
    mock_client.audio.transcriptions.create = AsyncMock(side_effect=RuntimeError("API error"))
    with pytest.raises(TranscriptionError):
        await stt.transcribe(b"fake-ogg-data")


@pytest.mark.asyncio
async def test_openai_tts_synthesis() -> None:
    """OpenAITTS delegates to AsyncOpenAI audio.speech.create."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.aread = AsyncMock(return_value=b"AUDIO_DATA_MP3")
    mock_client.audio.speech.create = AsyncMock(return_value=mock_resp)

    tts = OpenAITTS(model="tts-1", voice="alloy", client=mock_client)
    res = await tts.synthesize("Hello world", output_format="mp3")

    assert res.audio_bytes == b"AUDIO_DATA_MP3"
    assert res.mime_type == "audio/mp3"
    assert mock_client.audio.speech.create.await_count == 1
    call_kwargs = mock_client.audio.speech.create.await_args.kwargs
    assert call_kwargs["model"] == "tts-1"
    assert call_kwargs["voice"] == "alloy"
    assert call_kwargs["input"] == "Hello world"

    # Error handling
    mock_client.audio.speech.create = AsyncMock(side_effect=RuntimeError("Speech error"))
    with pytest.raises(SynthesisError):
        await tts.synthesize("Will fail")


@pytest.mark.asyncio
async def test_faster_whisper_stt() -> None:
    """FasterWhisperSTT wraps a WhisperModel and joins segments asynchronously."""
    mock_model = MagicMock()
    seg1 = MagicMock()
    seg1.text = "Hello"
    seg2 = MagicMock()
    seg2.text = "world"
    info = MagicMock()
    info.language = "en"
    info.duration = 2.5
    mock_model.transcribe.return_value = ([seg1, seg2], info)

    stt = FasterWhisperSTT(whisper_model=mock_model)
    res = await stt.transcribe(b"fake-audio-bytes")

    assert res.text == "Hello world"
    assert res.language == "en"
    assert res.duration_seconds == 2.5

    # Missing library error
    stt_uninstalled = FasterWhisperSTT()
    stt_uninstalled._model = None
    # If faster_whisper is not installed, _get_model raises ImportError
    # We can test this by checking exception type
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError) as exc_info:
            await stt_uninstalled.transcribe(b"bytes")
        assert "faster-whisper is not installed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_aws_polly_tts() -> None:
    """AmazonPollyTTS calls synthesize_speech on boto3 client."""
    mock_boto_client = MagicMock()
    mock_stream = io.BytesIO(b"POLLY_AUDIO_STREAM")
    mock_boto_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

    tts = AmazonPollyTTS(voice_id="Joanna", client=mock_boto_client)
    res = await tts.synthesize("Test speech", output_format="mp3")

    assert res.audio_bytes == b"POLLY_AUDIO_STREAM"
    assert res.mime_type == "audio/mp3"
    mock_boto_client.synthesize_speech.assert_called_once_with(
        Text="Test speech", OutputFormat="mp3", VoiceId="Joanna"
    )

    # Error handling
    mock_boto_client.synthesize_speech.side_effect = RuntimeError("AWS Polly error")
    with pytest.raises(SynthesisError):
        await tts.synthesize("Will fail")


@pytest.mark.asyncio
async def test_aws_transcribe_stt() -> None:
    """AmazonTranscribeSTT handles transcription via client."""
    mock_boto_client = MagicMock()
    mock_boto_client.transcribe_audio = MagicMock(return_value={"text": "Transcribed by AWS"})

    stt = AmazonTranscribeSTT(client=mock_boto_client)
    res = await stt.transcribe(b"audio-bytes-123", language="en-US")

    assert res.text == "Transcribed by AWS"

    # Default fallback
    mock_raw = MagicMock(spec=["start_transcription_job"])
    stt_default = AmazonTranscribeSTT(client=mock_raw)
    res_default = await stt_default.transcribe(b"12345")
    assert "processed 5 bytes" in res_default.text


def test_voice_factories() -> None:
    """Factory methods correctly instantiate providers by alias and raise on unknown."""
    assert isinstance(get_stt_provider("fake"), FakeSTT)
    assert isinstance(get_stt_provider("openai"), OpenAISTT)
    assert isinstance(get_stt_provider("whisper"), FasterWhisperSTT)
    assert isinstance(get_stt_provider("amazon"), AmazonTranscribeSTT)

    assert isinstance(get_tts_provider("fake"), FakeTTS)
    assert isinstance(get_tts_provider("openai"), OpenAITTS)
    assert isinstance(get_tts_provider("amazon"), AmazonPollyTTS)

    with pytest.raises(ValueError, match="Unknown STT provider"):
        get_stt_provider("unknown_stt")

    with pytest.raises(ValueError, match="Unknown TTS provider"):
        get_tts_provider("unknown_tts")
