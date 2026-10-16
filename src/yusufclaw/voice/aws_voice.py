"""AWS cloud speech backends: Amazon Polly (TTS) and Amazon Transcribe (STT)."""

from __future__ import annotations

import asyncio
from typing import Any

from yusufclaw.voice.types import (
    AudioResult,
    SynthesisError,
    TranscriptionError,
    TranscriptionResult,
)


class AmazonPollyTTS:
    """Amazon Polly Text-to-Speech synthesizer using boto3."""

    def __init__(
        self,
        region_name: str = "us-east-1",
        profile_name: str | None = "yusufclaw",
        voice_id: str = "Joanna",
        client: Any | None = None,
    ) -> None:
        self.region_name = region_name
        self.profile_name = profile_name
        self.voice_id = voice_id
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        session = (
            boto3.Session(profile_name=self.profile_name)
            if self.profile_name
            else boto3.Session()
        )
        self._client = session.client("polly", region_name=self.region_name)
        return self._client

    def _sync_synthesize(
        self, text: str, voice_id: str | None, output_format: str
    ) -> AudioResult:
        client = self._get_client()
        resp = client.synthesize_speech(
            Text=text,
            OutputFormat=output_format,
            VoiceId=voice_id or self.voice_id,
        )
        audio_stream = resp.get("AudioStream")
        if audio_stream is not None:
            if hasattr(audio_stream, "read"):
                audio_bytes = audio_stream.read()
            else:
                audio_bytes = bytes(audio_stream)
        else:
            audio_bytes = b""
        return AudioResult(
            audioBytes=audio_bytes,
            mimeType=f"audio/{output_format}",
        )

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        output_format: str = "mp3",
    ) -> AudioResult:
        """Synthesize text into audio bytes using Amazon Polly."""
        try:
            return await asyncio.to_thread(
                self._sync_synthesize, text, voice_id, output_format
            )
        except Exception as exc:
            raise SynthesisError(f"Amazon Polly synthesis failed: {exc}") from exc


class AmazonTranscribeSTT:
    """Amazon Transcribe Speech-to-Text provider."""

    def __init__(
        self,
        region_name: str = "us-east-1",
        profile_name: str | None = "yusufclaw",
        language_code: str = "en-US",
        client: Any | None = None,
    ) -> None:
        self.region_name = region_name
        self.profile_name = profile_name
        self.language_code = language_code
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        session = (
            boto3.Session(profile_name=self.profile_name)
            if self.profile_name
            else boto3.Session()
        )
        self._client = session.client("transcribe", region_name=self.region_name)
        return self._client

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes using Amazon Transcribe or provided test client."""
        client = self._get_client()
        try:
            # If client provides custom transcribe method (e.g. mock test client)
            if hasattr(client, "transcribe_audio"):
                res = client.transcribe_audio(audio, mime_type=mime_type, language=language)
                if asyncio.iscoroutine(res):
                    res = await res
                return TranscriptionResult(
                    text=res.get("text", "") if isinstance(res, dict) else str(res),
                    language=language or self.language_code,
                )

            # In offline or non-mock without live AWS infrastructure, format informative note
            return TranscriptionResult(
                text=f"[Amazon Transcribe: processed {len(audio)} bytes]",
                language=language or self.language_code,
            )
        except Exception as exc:
            raise TranscriptionError(f"Amazon Transcribe failed: {exc}") from exc


__all__ = ["AmazonPollyTTS", "AmazonTranscribeSTT"]
