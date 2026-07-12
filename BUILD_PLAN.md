# Resonode — Build Plan & Architecture

**Status: Phases 1–4 built** (foundation, streaming, LLM features, vector-RAG
memory v1) — see the [Roadmap](#roadmap) below. This is the map from today's
local transcription core to the full real-time voice + memory platform in the
README.

## Design principles

- **Local-first & private by default.** Cloud / paid backends are opt-in.
- **Everything pluggable** behind one interface — STT, LLM, memory, storage.
- **SQLite now → Postgres + pgvector** for the hosted tier, behind the same
  `Storage` API. Cogs never call a vendor SDK directly; they call interfaces.

## Architecture

```
Discord voice receive
        │  live audio, per speaker
        ▼
UtteranceChunker (VAD)        resonode/audio.py     ← batch → streaming seam
        │  utterance buffers
        ▼
STT  (LocalWhisper | Deepgram)   resonode/stt.py
        │  Segments (text + timestamps)
        ▼
Storage  (sessions, utterances)  resonode/storage.py   ← SQLite → Postgres
        │
        ├─► Transcripts / search           cogs/query.py
        ├─► LLM: summarize/translate/mod    resonode/services.py
        └─► Memory: recall (RAG)            resonode/services.py
```

### Module map

| File | Responsibility |
| --- | --- |
| `resonode/config.py` | Env-driven config; local-first defaults. |
| `resonode/storage.py` | Sessions, utterances, per-guild settings (SQLite; Postgres-ready API). |
| `resonode/transcripts.py` | Timestamp + transcript formatting (pure). |
| `resonode/audio.py` | `UtteranceChunker` — RMS/VAD segmentation; the streaming seam. |
| `resonode/streaming.py` | `SpeakerStream` + `StreamingSink` — live per-speaker capture (Phase 2). |
| `resonode/stt.py` | `BaseSTT`, `LocalWhisperSTT` (default), `DeepgramSTT` (stub), `get_stt()`. |
| `resonode/services.py` | `BaseLLM` (None/OpenAI/Anthropic) + `BaseMemory` (keyword now, vector later). |
| `resonode/cogs/recording.py` | `/join`, `/leave`, `/ping` → record → transcribe → persist → post (batch). |
| `resonode/cogs/streaming.py` | Live `/join`, `/leave` (loaded when `RESONODE_STREAMING=true`). |
| `resonode/cogs/query.py` | `/transcript`, `/search`, `/summary`, `/ask`. |
| `resonode/bot.py` | Entrypoint: wires config + services + cogs, runs the bot. |
| `bot.py` (root) | Thin launcher → `resonode.bot:main` (keeps `start.bat` working). |
| `tests/test_core.py` | Sandbox unit tests (no Discord needed). |

## Commands

| Command | Status | Notes |
| --- | --- | --- |
| `/join` / `/leave` | ✅ | Record a session; transcribe + persist + post. |
| `/ping` | ✅ | Liveness. |
| `/transcript` | ✅ | Re-post the latest session transcript. |
| `/search` | ✅ | Keyword search the server's voice history. |
| `/summary` | ⚙️ | Needs an LLM configured (`RESONODE_LLM`). |
| `/ask` | ⚙️ | Keyword recall now; synthesized answers when an LLM is set. |

## Roadmap

- **Phase 1 — Foundation (DONE).** Package, config, storage, pluggable
  STT/LLM/memory seams, ported recording, search/ask over history, unit tests.
- **Phase 2 — Real-time streaming (DONE — validated live 2026-07-04).** Custom
  voice sink → `UtteranceChunker` → STT per finished utterance → live
  transcript lines. Enable with `RESONODE_STREAMING=true`. Runs on the
  py-cord ≥ 2.9 DAVE receive API (`VoiceData.pcm`, Member-keyed speakers);
  idle/VAD thresholds are env-tunable (`RESONODE_STREAM_IDLE_MS`,
  `RESONODE_VAD_*`). Pure logic + sink shapes are unit-tested.
- **Phase 3 — LLM features live (BUILT — live validation with an API key
  pending).** Shipped 2026-07-04: `/translate` live translation and
  `/moderation` voice alerts (streaming mode, per-guild persisted settings),
  plus auto-summary on `/leave` in both modes via `RESONODE_AUTO_SUMMARY`.
  All provider-agnostic behind `BaseLLM` (`RESONODE_LLM=openai|anthropic`).
- **Phase 4 — Server brain (vector RAG) (v1 BUILT).** `VectorMemory` shipped
  2026-07-04: pluggable embeddings (`resonode/embeddings.py` — local fastembed
  by default, OpenAI opt-in), vectors in SQLite, numpy cosine top-k, lazy
  self-backfilling index, keyword fallback. Enable with
  `RESONODE_MEMORY=vector` + `pip install fastembed`. `/ask` answers cite
  [timestamp] speaker via the existing LLM prompt. Hosted-tier upgrade path:
  pgvector/Qdrant behind the same `Storage`/`BaseMemory` seams; optional
  Mem0/Zep adapter still on the roadmap.
- **Phase 5 — Hosting & scale.** Always-on host, Postgres, per-guild
  concurrency, GPU Whisper or Deepgram for the low-latency tier.
- **Phase 6 — Monetize.** Discord App Directory + Premium Apps subscription /
  entitlement checks (or license keys); tier flags live in `guild_settings`.

## How to extend

- **New STT backend:** subclass `BaseSTT`, return it from `get_stt()`.
- **New LLM provider:** subclass `_ChatLLM`, add to `get_llm()`.
- **New embedding backend:** subclass `BaseEmbedder` in `resonode/embeddings.py`,
  return it from `get_embedder()`. Vector memory itself is live —
  `RESONODE_MEMORY=vector`.

## Testing

`python tests/test_core.py` covers config, storage, transcripts, the chunker,
and the STT/LLM/memory factories — no Discord required. The live voice paths
need a Discord token and a test server.
