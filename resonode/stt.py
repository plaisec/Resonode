"""Speech-to-text behind one interface. Local Whisper is the default; Deepgram
is a pluggable cloud backend (stubbed). Add a backend by subclassing BaseSTT.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    lang: Optional[str] = None


def _pcm_to_wav(pcm: bytes, sample_rate: int, channels: int, sample_width: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return path


class BaseSTT(ABC):
    name = "base"

    @abstractmethod
    async def transcribe_file(self, path: str) -> list[Segment]:
        ...

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int, channels: int = 1,
                             sample_width: int = 2) -> list[Segment]:
        path = _pcm_to_wav(pcm, sample_rate, channels, sample_width)
        try:
            return await self.transcribe_file(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class LocalWhisperSTT(BaseSTT):
    """faster-whisper on local hardware. Model loads lazily on first use."""
    name = "local-whisper"

    def __init__(self, model: str = "base", device: str = "cpu", compute_type: str = "int8",
                 language: str = ""):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language or ""  # pinned ISO code, or "" for auto-detect
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # lazy import
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
        return self._model

    def _transcribe_sync(self, path: str) -> list[Segment]:
        model = self._ensure_model()
        # Pinning the language skips Whisper's auto-detect, which misfires on
        # short clips (randomly "detecting" French and the like).
        segments, info = model.transcribe(path, vad_filter=True,
                                          language=self.language or None)
        lang = getattr(info, "language", None)
        return [
            Segment(seg.start, seg.end, seg.text.strip(), lang=lang)
            for seg in segments
            if seg.text.strip()
        ]

    async def transcribe_file(self, path: str) -> list[Segment]:
        return await asyncio.to_thread(self._transcribe_sync, path)


class DeepgramSTT(BaseSTT):
    """Cloud streaming STT — pluggable backend, stubbed for now."""
    name = "deepgram"

    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key

    async def transcribe_file(self, path: str) -> list[Segment]:
        raise NotImplementedError(
            "Deepgram backend is stubbed. Add `deepgram-sdk`, implement transcribe_file/"
            "streaming here, or set RESONODE_STT=local to use Whisper."
        )


def get_stt(config) -> BaseSTT:
    backend = (getattr(config, "stt_backend", "local") or "local").lower()
    if backend == "deepgram":
        return DeepgramSTT(getattr(config, "deepgram_api_key", None))
    return LocalWhisperSTT(config.whisper_model, config.whisper_device, config.compute_type,
                           getattr(config, "stt_language", ""))
