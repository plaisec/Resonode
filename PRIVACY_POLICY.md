# Resonode — Privacy Policy

**Last updated: July 4, 2026**

This Privacy Policy explains how the Resonode Discord application ("Resonode,"
"the bot," "we") handles information when it is added to a Discord server.

Resonode is **open-source and self-hostable**. When you run your own instance,
**you** are the operator and data controller for that instance, and this policy
describes the behavior of the published software. The operator of the instance
you interact with is responsible for honoring it.

- **Operator:** the Resonode maintainers
- **Contact:** via GitHub Issues on the project repository — https://github.com/jlanguell/resonode/issues

---

## 1. What Resonode does

When invited to a server and instructed to join a voice channel (via the
`/join` command), Resonode records the voice session and produces a
speaker-attributed, timestamped text transcript. It also responds to slash
commands such as `/leave`, `/ping`, `/transcript`, and `/search`.

## 2. Information we process

- **Voice audio** — captured only while actively recording a voice channel
  after an explicit `/join`. Audio is **transient**: it is written to temporary
  per-speaker files only for the duration of transcription and then deleted
  automatically.
- **Transcripts** — the text produced from voice audio, with timestamps.
- **Discord identifiers** — user IDs, usernames/display names (used to attribute
  transcript lines to speakers), server (guild) IDs, and channel IDs.
- **Command inputs** — the slash commands you run and any text you pass to them
  (e.g., the words you give `/search`).

Resonode uses **slash commands**, so it does **not** read general message
content — it only receives the slash-command inputs directed at it, and does not
collect payment information.

## 3. How information is processed

- **Local-first by default.** In the default configuration, speech-to-text runs
  **locally** on the operator's host using faster-whisper. **Audio never leaves
  the operator's machine** and no third-party AI service receives your voice.
- **Optional cloud backends (operator opt-in).** An operator may configure
  optional services. When enabled, the relevant data is sent to that provider
  and handled under **their** privacy policy:
  - **Deepgram** (cloud speech-to-text) — receives voice audio. https://deepgram.com/privacy
  - **OpenAI** (summaries / translation / moderation / embeddings) — receives transcript text. https://openai.com/policies/privacy-policy
  - **Anthropic** (summaries / translation / moderation) — receives transcript text. https://www.anthropic.com/legal/privacy

  The default **vector memory** backend computes embeddings locally
  (fastembed) — transcript text stays on the operator's machine unless the
  OpenAI embeddings backend is explicitly enabled.
  These are **off by default**. If the instance you use has them enabled, the
  operator must disclose it.
- **Discord.** All interaction is delivered through Discord and is also subject
  to [Discord's Privacy Policy](https://discord.com/privacy).

## 4. Storage and retention

- **Temporary audio** is stored only during transcription and deleted
  immediately afterward.
- **Transcripts and session metadata** are stored in the operator's own
  database (SQLite by default) on the operator's infrastructure. Retention is
  controlled by the operator. There is no central Resonode-operated database in
  the open-source build.
- Operators should define and disclose their own retention period. By default
  the software does **not** automatically delete transcripts; they remain in the
  operator's database until the operator removes them.

## 5. Consent and recording laws

Voice recording is sensitive and may be regulated where you live (including
one-party / all-party consent laws). **Recording must only occur with the
knowledge and consent of everyone in the channel.** The server's administrators
and the participants — not the Resonode software — are responsible for obtaining
consent and complying with applicable law. See the Terms of Service.

## 6. How information is shared

We do **not** sell personal information. Data is shared only with the
infrastructure and optional providers described in Section 3, and only as needed
to operate the features the operator has enabled.

## 7. Your choices and rights

- Don't want to be recorded? Leave the voice channel, or ask the server's
  administrators not to record.
- To request access to or deletion of transcripts that mention you, contact the
  operator at the address above. Because instances are self-hosted, only the
  operator of a given instance can fulfill such requests.

## 8. Children

Resonode is not directed to children. You must meet Discord's minimum age
requirement (13+, or older where required by local law) to use Discord and this
app.

## 9. Security

Operators are expected to keep their host, database, and bot token secure.
No method of transmission or storage is 100% secure; we cannot guarantee
absolute security.

## 10. Changes to this policy

We may update this policy as Resonode's features evolve (for example, when
hosted or memory features ship). Material changes will be reflected by updating
the "Last updated" date above.

## 11. Contact

Questions about this policy: open a GitHub issue at
https://github.com/jlanguell/resonode/issues. (Issues are public — for
anything involving your personal data, say so and we'll arrange a private
channel.)
