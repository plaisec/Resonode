"""Sandbox unit tests for Resonode's pure-logic core (no Discord needed)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resonode.config import Config
from resonode.storage import Storage, Utterance
from resonode.transcripts import format_timestamp, render_transcript
from resonode.audio import UtteranceChunker
from resonode.stt import get_stt, LocalWhisperSTT, DeepgramSTT, _pcm_to_wav
from resonode.services import get_llm, NoLLM, get_memory
from resonode.streaming import SpeakerStream


class _Cfg:
    stt_backend = "local"; whisper_model = "base"; whisper_device = "cpu"; compute_type = "int8"
    deepgram_api_key = None; llm_provider = "none"; openai_api_key = None
    anthropic_api_key = None; llm_model = ""


def _pcm(seconds, amp, rate=16000):
    import numpy as np
    return np.full(int(rate * seconds), amp, dtype=np.int16).tobytes()


def test_config():
    c = Config.from_env()
    assert c.prefix
    assert c.stt_backend in ("local", "deepgram")
    assert c.compute_type in ("int8", "float16")


def test_transcripts():
    assert format_timestamp(3661) == "01:01:01"
    out = render_transcript([{"start_s": 4, "speaker_name": "Alice", "text": "hi"}], "H")
    assert out.startswith("H") and "[00:00:04] Alice: hi" in out


def test_storage_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        st = Storage(os.path.join(d, "t.db"))
        sid = st.start_session(1, "G", 2, "vc", "base")
        st.add_utterance(sid, 1, Utterance("Alice", 0.0, 1.0, "hello world"))
        st.add_utterance(sid, 1, Utterance("Bob", 1.0, 2.0, "let's ship resonode"))
        st.end_session(sid)
        assert len(st.session_utterances(sid)) == 2
        assert st.latest_session(1)["id"] == sid
        assert len(st.search(1, "resonode")) == 1
        assert len(st.recent_utterances(1)) == 2
        st.set_setting(1, "lang", "en")
        assert st.get_setting(1, "lang") == "en"
        st.close()


def test_chunker():
    ch = UtteranceChunker(sample_rate=16000, channels=1, silence_rms=300,
                          min_silence_ms=200, min_utterance_ms=100)
    assert ch.has_pending is False
    assert ch.feed(_pcm(0.3, 6000)) == []     # still talking
    assert ch.has_pending is True
    out = ch.feed(_pcm(0.3, 0))               # pause closes the utterance
    assert len(out) == 1 and len(out[0]) > 0
    assert ch.has_pending is False


def test_speaker_stream():
    ss = SpeakerStream(UtteranceChunker(sample_rate=16000, channels=1, silence_rms=300,
                                        min_silence_ms=200, min_utterance_ms=100))
    assert ss.feed(_pcm(0.3, 6000)) == []           # buffering
    assert ss.flush_if_idle(10_000) is None         # not idle long enough
    ss.last_activity -= 1.0                          # pretend a second passed
    out = ss.flush_if_idle(500)                      # now idle -> flush
    assert out is not None and len(out) > 0
    assert SpeakerStream().flush_if_idle(500) is None  # nothing pending -> None


def test_stt_factory():
    s = get_stt(_Cfg())
    assert isinstance(s, LocalWhisperSTT) and s.name == "local-whisper"
    assert s.language == ""                      # auto-detect by default
    c = _Cfg(); c.stt_language = "en"
    assert get_stt(c).language == "en"           # RESONODE_STT_LANGUAGE pins it
    d = _Cfg(); d.stt_backend = "deepgram"
    assert isinstance(get_stt(d), DeepgramSTT)
    p = _pcm_to_wav(_pcm(0.1, 0), 16000, 1, 2)
    assert os.path.exists(p)
    os.unlink(p)


def test_streaming_sink_write_shapes():
    """StreamingSink handles py-cord >= 2.9 (VoiceData/Member) and legacy shapes."""
    import types
    try:
        import discord  # noqa: F401 — use the real library when installed
    except ImportError:  # stub the minimal surface so this test stays Discord-free
        sinks = types.ModuleType("discord.sinks")

        class _Sink:
            __sink_listeners__ = []

            def __init__(self, *a, **k):
                self.audio_data = {}

        sinks.Sink = _Sink
        stub = types.ModuleType("discord")
        stub.sinks = sinks
        sys.modules["discord"] = stub
        sys.modules["discord.sinks"] = sinks

    from resonode.streaming import make_streaming_sink

    got = []
    factory = lambda: UtteranceChunker(sample_rate=16000, channels=1, silence_rms=300,
                                       min_silence_ms=200, min_utterance_ms=100)
    sink = make_streaming_sink(lambda uid, pcm: got.append((uid, len(pcm))), factory)

    member = types.SimpleNamespace(id=42)                        # Member-shaped
    sink.write(types.SimpleNamespace(pcm=_pcm(0.3, 6000)), member)  # talking
    sink.write(types.SimpleNamespace(pcm=_pcm(0.3, 0)), member)     # pause closes it
    assert got and got[0][0] == 42 and got[0][1] > 0

    sink.write(types.SimpleNamespace(pcm=_pcm(0.3, 6000)), None)  # unmapped SSRC
    assert len(got) == 1                                          # ignored, no crash

    n = len(got)
    sink.write(_pcm(0.3, 6000), 7)                                # legacy bytes + int id
    sink.write(_pcm(0.3, 0), 7)
    assert got[n][0] == 7

    assert sink.flush_all() == []                                 # nothing left buffered


def test_vector_memory():
    import numpy as np
    from resonode.services import VectorMemory

    class FakeEmbedder:
        name, model = "fake", "fake-1"

        def embed(self, texts):
            vecs = []
            for t in texts:
                t = t.lower()
                v = np.array([float("launch" in t), float("pizza" in t), 1e-3], dtype=np.float32)
                vecs.append(v / np.linalg.norm(v))
            return np.vstack(vecs)

    with tempfile.TemporaryDirectory() as d:
        st = Storage(os.path.join(d, "v.db"))
        sid = st.start_session(9, "G", 1, "vc", "base")
        uid = st.add_utterance(sid, 9, Utterance("Al", 0, 1, "we decided to launch on friday"))
        assert isinstance(uid, int) and uid > 0          # add_utterance returns row id now
        st.add_utterance(sid, 9, Utterance("Bo", 1, 2, "pizza party tomorrow night"))
        mem = VectorMemory(st, FakeEmbedder())
        hits = mem.recall(9, "when do we launch?", k=1)
        assert len(hits) == 1 and "launch" in hits[0]["text"]
        assert st.unembedded_utterances(9, "fake-1") == []  # lazily backfilled
        assert "pizza" in mem.recall(9, "pizza", k=1)[0]["text"]
        st.close()


def test_llm_translate_seam():
    from resonode.services import _ChatLLM, NoLLM
    calls = {}

    class FakeLLM(_ChatLLM):
        name = "fake"

        def _chat(self, system, user):
            calls["system"], calls["user"] = system, user
            return "hola mundo"

    out = FakeLLM().translate("hello world", "Spanish")
    assert out == "hola mundo"
    assert "Spanish" in calls["system"] and calls["user"] == "hello world"
    assert "No LLM configured" in NoLLM().translate("hi", "en")


def test_llm_moderate_seam():
    from resonode.services import _ChatLLM, NoLLM

    class Flagger(_ChatLLM):
        name = "fake"

        def __init__(self, reply):
            self.reply = reply

        def _chat(self, system, user):
            return self.reply

    v = Flagger("FLAG: threats of violence").moderate("bad words")
    assert v["flagged"] is True and "threats" in v["reason"]
    v = Flagger("SAFE").moderate("hello friends")
    assert v["flagged"] is False
    assert NoLLM().moderate("anything")["flagged"] is False


def test_llm_and_memory():
    llm = get_llm(_Cfg())
    assert isinstance(llm, NoLLM) and not llm.available
    assert "No LLM configured" in llm.summarize("x")
    with tempfile.TemporaryDirectory() as d:
        st = Storage(os.path.join(d, "m.db"))
        sid = st.start_session(7, "G", 1, "vc", "base")
        st.add_utterance(sid, 7, Utterance("Al", 0, 1, "we decided to launch on friday"))
        mem = get_memory(_Cfg(), st)
        hits = mem.recall(7, "launch friday", k=5)
        assert any("friday" in h["text"] for h in hits)
        st.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print("ok", f.__name__)
    print(f"ALL {len(fns)} TESTS PASSED")
