"""Real-time streaming transcription — the Phase-2 seam made live.

A custom py-cord voice Sink routes each speaker's PCM into a per-speaker
UtteranceChunker; finished utterances are handed to the bot's event loop for
transcription as they complete, instead of waiting for /leave.

Threading: py-cord calls Sink.write() from its decode thread, so we never touch
asyncio there — completed utterances are pushed to the loop via a thread-safe
callback supplied by the cog. SpeakerStream is pure (no Discord) and unit-tested.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .audio import UtteranceChunker

# Discord delivers 48 kHz, 16-bit, stereo PCM per speaker.
STREAM_RATE = 48000
STREAM_CHANNELS = 2
STREAM_WIDTH = 2


class SpeakerStream:
    """Per-speaker buffer: feeds PCM to a chunker, supports idle flushing.

    Thread-safe: feed() runs on the decode thread; flush_if_idle()/flush() run on
    the event loop's watchdog.
    """

    def __init__(self, chunker: Optional[UtteranceChunker] = None):
        self.chunker = chunker or UtteranceChunker()
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()

    def feed(self, pcm: bytes) -> list[bytes]:
        with self.lock:
            self.last_activity = time.monotonic()
            return self.chunker.feed(pcm)

    def idle_ms(self, now: Optional[float] = None) -> float:
        return ((now if now is not None else time.monotonic()) - self.last_activity) * 1000.0

    def flush_if_idle(self, idle_ms: float, now: Optional[float] = None) -> Optional[bytes]:
        with self.lock:
            if self.chunker.has_pending and self.idle_ms(now) >= idle_ms:
                data = self.chunker.flush()
                return data or None
            return None

    def flush(self) -> Optional[bytes]:
        with self.lock:
            data = self.chunker.flush()
            return data or None


def make_streaming_sink(on_utterance: Callable[[int, bytes], None],
                        chunker_factory: Callable[[], UtteranceChunker] = UtteranceChunker):
    """Build a StreamingSink. py-cord is imported lazily so this module stays
    importable (and SpeakerStream unit-testable) without it.

    INTEGRATION NOTE: py-cord calls Sink.write(data, user) from its decode thread.
    On py-cord >= 2.9 (DAVE receive rework), `data` is a VoiceData with `.pcm`
    and `user` is a Member/User object (or None before the SSRC is mapped).
    On older versions, `data` was PCM bytes or a RawData with `.decoded_data`
    and `user` was an int id. We handle all shapes.
    """
    import discord

    class StreamingSink(discord.sinks.Sink):
        def __init__(self):
            super().__init__()
            self._on_utterance = on_utterance
            self._chunker_factory = chunker_factory
            self._streams: dict[int, SpeakerStream] = {}
            self._lock = threading.Lock()

        def _stream(self, uid: int) -> SpeakerStream:
            with self._lock:
                s = self._streams.get(uid)
                if s is None:
                    s = SpeakerStream(self._chunker_factory())
                    self._streams[uid] = s
                return s

        def write(self, data, user):
            uid = getattr(user, "id", user)
            if uid is None:
                return  # SSRC not yet mapped to a user; can't attribute audio.
            pcm = getattr(data, "pcm", None)  # py-cord >= 2.9 VoiceData
            if pcm is None:
                pcm = getattr(data, "decoded_data", None)  # legacy RawData
            if pcm is None:
                try:
                    pcm = bytes(data)
                except Exception:
                    return
            if not pcm:
                return
            try:
                done = self._stream(uid).feed(pcm)
            except Exception:
                return
            for chunk in done:
                if chunk:
                    self._on_utterance(uid, chunk)

        def flush_idle(self, idle_ms: float):
            with self._lock:
                streams = list(self._streams.items())
            out = []
            for uid, s in streams:
                pcm = s.flush_if_idle(idle_ms)
                if pcm:
                    out.append((uid, pcm))
            return out

        def flush_all(self):
            with self._lock:
                streams = list(self._streams.items())
            out = []
            for uid, s in streams:
                pcm = s.flush()
                if pcm:
                    out.append((uid, pcm))
            return out

        def cleanup(self):
            # py-cord calls this on stop; nothing buffered to disk.
            pass

    return StreamingSink()
