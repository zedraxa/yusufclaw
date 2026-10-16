"""Local STT implementation using faster-whisper (optional dependency)."""

from __future__ import annotations

import asyncio
import io
from typing import Any

from yusufclaw.voice.types import (
    TranscriptionError,
    TranscriptionResult,
)


class FasterWhisperSTT:
    """Local offline speech-to-text using faster-whisper."""

    def __init__(
        self,
        model_size_or_path: str = "base",
        device: str = "auto",
        compute_type: str = "default",
        whisper_model: Any | None = None,
    ) -> None:
        self.model_size_or_path = model_size_or_path
        self.device = device
        self.compute_type = compute_type
        self._model = whisper_model

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "faster-whisper is not installed. Install with: "
                "uv pip install faster-whisper or uv add 'faster-whisper>=1.0.0'"
            ) from exc

        self._model = WhisperModel(
            self.model_size_or_path,
            device=self.device,
            compute_type=self.compute_type,
        )
        return self._model

    def _sync_transcribe(
        self, audio: bytes, language: str | None = None
    ) -> TranscriptionResult:
        model = self._get_model()
        audio_stream = io.BytesIO(audio)
        segments, info = model.transcribe(audio_stream, language=language)
        transcribed_text = " ".join(s.text.strip() for s in segments if s.text.strip())
        return TranscriptionResult(
            text=transcribed_text,
            language=getattr(info, "language", language),
            durationSeconds=getattr(info, "duration", None),
        )

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Run faster-whisper transcription asynchronously in a worker thread."""
        try:
            return await asyncio.to_thread(self._sync_transcribe, audio, language)
        except ImportError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"faster-whisper transcription failed: {exc}") from exc


__all__ = ["FasterWhisperSTT"]
