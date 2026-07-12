# Changelog

All notable changes to Resonode are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] — 2026-07-09

### Changed
- **Translation prompt hardened for speech input** — the LLM is now told each
  line is noisy live speech-to-text (missing punctuation, slang, occasional
  mis-transcriptions), so it translates intended meaning instead of rendering
  transcription errors literally.
- **`/join` now coaches audio setup** — both batch and streaming modes append
  a subtext tip recommending Discord's **Voice Isolation** input profile, which
  gates the mic to silence between phrases so the RMS-based utterance chunker
  splits lines cleanly.

### Fixed
- **`resonode.__version__` was stale at `0.1.0`** — now synced with
  `version.txt` (single source going forward: bump both together).

### Documentation
- **README Quickstart restructured** — one-time install (venv, deps, DAVE
  py-cord build with a plain-language explanation of *why* it's required) is
  now separate from "Run the bot" (`start.bat` is the whole story; the console
  window is the bot).
- **New "Audio input tips" README section** — Discord Input Profile guidance
  (Voice Isolation vs Studio vs Custom and how each interacts with the VAD),
  voice-channel bitrate as the server-side quality cap, Voice Activity vs
  Push-to-Talk, echo cancellation for speaker users, and disabling Windows
  device-level audio enhancements.
- **English-only Whisper variants documented** — `.en` and `distil-*.en`
  models recommended for English servers (more accurate at the same size),
  paired with `RESONODE_STT_LANGUAGE=en`; noted in README and `.env.example`.

## [0.2.0] — 2026-07-04

### Fixed
- **Voice receive under Discord's DAVE E2EE protocol** (enforced by Discord on
  2026-03-02, which broke voice reception on py-cord ≤ 2.8.0). Resonode now
  runs against the py-cord DAVE-receive build
  ([pycord PR #3159](https://github.com/Pycord-Development/pycord/pull/3159));
  see the install note in `requirements.txt`. Adds the `davey` dependency
  (Discord's DAVE protocol, Rust/OpenMLS implementation).
- **Batch recording compatibility with the new py-cord voice receive API** —
  `sink.audio_data` is now keyed by `Member`/`User` objects instead of integer
  user ids; speaker ids are normalized before storage (previously crashed with
  `sqlite3.ProgrammingError` and dropped the transcript).
- **`/leave` hardening** — responds immediately, force-disconnects even when
  recording state is stale (dropped connection, kicked mid-session), and a
  failed disconnect no longer aborts transcription.

### Added
- **`RESONODE_STT_LANGUAGE`** — pin Whisper's transcription language (e.g.
  `en`) instead of per-clip auto-detect, which can misfire on short
  utterances. Blank keeps auto-detect; `.env.example` now ships with `en`.
- **Env-tunable streaming knobs** — `RESONODE_STREAM_IDLE_MS` and
  `RESONODE_VAD_{SILENCE_RMS,MIN_SILENCE_MS,MIN_UTTERANCE_MS,MAX_UTTERANCE_MS}`
  so live VAD tuning needs no code edits (see `.env.example`).
- **Unit coverage for the new sink API** — `StreamingSink.write()` shapes
  (VoiceData/Member, unmapped-SSRC `None`, legacy bytes/int) tested without
  Discord installed.
- **Live translation (Phase 3)** — `/translate <language> [#channel]` in
  streaming mode posts an LLM-translated line for each utterance as it's
  spoken (`off` disables). Per-guild setting, persisted across sessions;
  provider-agnostic (`RESONODE_LLM=openai|anthropic`). Skips lines already in
  the target language when ISO codes match.
- **Auto-summary on `/leave` (Phase 3)** — with `RESONODE_AUTO_SUMMARY=true`
  and an LLM configured, both batch and streaming sessions post a
  decisions/action-items summary after the transcript.
- **AI voice-moderation alerts (Phase 3)** — `/moderation on|off [#channel]`
  in streaming mode; flagged utterances alert the chosen channel with the
  quoted line and reason. Per-guild setting, persisted; runs concurrently
  with translation.
- **Vector "server brain" v1 (Phase 4)** — `RESONODE_MEMORY=vector` gives
  `/ask` semantic recall: local fastembed embeddings (ONNX — private, free)
  or OpenAI embeddings, vectors stored in SQLite, cosine top-k via numpy.
  Existing history backfills itself lazily on first recall; keyword recall
  stays the default and the automatic fallback.

### Changed
- **Streaming mode ported to the new py-cord receive API** — the custom
  `StreamingSink` now reads `VoiceData.pcm` and Member-keyed speakers (legacy
  shapes still handled); unattributed audio is skipped. Streaming `/leave`
  hardened to batch parity (stale-state force-disconnect + session cleanup).
  Validated live in a voice channel (2026-07-04).

## [0.1.0] — 2026-06-27

### Added
- **Phase 1 foundation** — env-driven config, SQLite storage, and pluggable
  STT / LLM / memory interfaces so backends stay swappable.
- **Voice transcription core** — `/join` records a voice channel and `/leave`
  posts a speaker-attributed, timestamped transcript, transcribed **locally**
  with faster-whisper (no API key, audio never leaves your machine).
- **Slash commands** — `/join`, `/leave`, `/ping`, plus `/transcript`,
  `/search`, `/summary`, and `/ask` over stored history. No privileged Message
  Content intent required.
- **Streaming prototype** — near-real-time, per-utterance transcription behind
  `RESONODE_STREAMING=true`.
- **Brand kit** — original voice-core + memory-constellation icons and banners
  in `assets/resonode/`.
- **Project docs & infra** — Privacy Policy, Terms of Service, dressed-up README,
  build plan, GitHub Actions CI, Dependabot, issue templates, and contributor /
  security guides.

[0.2.1]: https://github.com/jlanguell/resonode/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jlanguell/resonode/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jlanguell/resonode/releases/tag/v0.1.0
