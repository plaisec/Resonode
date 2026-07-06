"""Live transcription cog — /join, /leave that stream lines as people speak.

Loaded instead of RecordingCog when RESONODE_STREAMING=true. Each finished
utterance is transcribed and posted live, and persisted so /search and /ask work on
streamed sessions too. A full transcript file is posted when the session ends.
"""
from __future__ import annotations

import asyncio
import io
import time
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

from ..audio import UtteranceChunker
from ..storage import Utterance
from ..streaming import make_streaming_sink, STREAM_RATE, STREAM_CHANNELS, STREAM_WIDTH
from ..transcripts import render_transcript, format_timestamp

IDLE_FLUSH_MS = 700      # default; override with RESONODE_STREAM_IDLE_MS
WATCHDOG_INTERVAL = 0.4  # seconds between idle checks


class StreamingCog(commands.Cog):
    def __init__(self, bot, config, stt, storage, llm=None):
        self.bot = bot
        self.config = config
        self.stt = stt
        self.storage = storage
        self.llm = llm
        self.sessions: dict[int, dict] = {}
        self.tx_dir = Path(config.transcripts_dir)
        self.tx_dir.mkdir(parents=True, exist_ok=True)
        self.idle_flush_ms = float(getattr(config, "stream_idle_flush_ms", IDLE_FLUSH_MS))

    def _make_chunker(self) -> UtteranceChunker:
        cfg = self.config
        return UtteranceChunker(
            silence_rms=getattr(cfg, "vad_silence_rms", 500.0),
            min_silence_ms=getattr(cfg, "vad_min_silence_ms", 600.0),
            min_utterance_ms=getattr(cfg, "vad_min_utterance_ms", 400.0),
            max_utterance_ms=getattr(cfg, "vad_max_utterance_ms", 15000.0),
        )

    @discord.slash_command(name="join", description="Join your voice channel and transcribe live.")
    async def join(self, ctx: discord.ApplicationContext):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server voice channel.", ephemeral=True)
            return
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.respond("You need to be in a voice channel first.", ephemeral=True)
            return
        if ctx.guild.id in self.sessions:
            await ctx.respond("I'm already live in this server. Use `/leave` to stop.", ephemeral=True)
            return
        channel = ctx.author.voice.channel
        await ctx.defer()
        try:
            vc = await channel.connect()
        except discord.ClientException:
            await ctx.respond("I'm already connected to a voice channel here.")
            return

        loop = asyncio.get_running_loop()
        session_id = self.storage.start_session(
            ctx.guild.id, ctx.guild.name, channel.id, channel.name, self.config.whisper_model
        )
        queue: asyncio.Queue = asyncio.Queue()

        def on_utterance(uid: int, pcm: bytes):  # decode thread -> loop
            loop.call_soon_threadsafe(queue.put_nowait, (uid, pcm))

        sink = make_streaming_sink(on_utterance, self._make_chunker)
        t_lang = self.storage.get_setting(ctx.guild.id, "translate_lang", "") or None
        t_chan = None
        if t_lang:
            cid = self.storage.get_setting(ctx.guild.id, "translate_channel_id")
            t_chan = (ctx.guild.get_channel(int(cid)) if cid else None) or ctx.channel
        m_on = (self.storage.get_setting(ctx.guild.id, "moderation_on", "") or "") == "1"
        m_chan = None
        if m_on:
            mcid = self.storage.get_setting(ctx.guild.id, "moderation_channel_id")
            m_chan = (ctx.guild.get_channel(int(mcid)) if mcid else None) or ctx.channel
        state = {
            "vc": vc, "sink": sink, "session_id": session_id,
            "channel": ctx.channel, "guild": ctx.guild,
            "start": time.monotonic(), "queue": queue,
            "translate_lang": t_lang, "translate_channel": t_chan,
            "moderate": m_on, "moderation_channel": m_chan,
        }
        self.sessions[ctx.guild.id] = state
        vc.start_recording(sink, self._on_finish, ctx.channel)
        state["consumer"] = asyncio.create_task(self._consume(ctx.guild.id))
        state["watchdog"] = asyncio.create_task(self._watchdog(ctx.guild.id))
        await ctx.respond(
            f"🔴 **Live transcription** started for **{channel.name}** — lines appear here "
            f"as people speak. `/leave` to stop."
        )

    @discord.slash_command(name="leave", description="Stop live transcription and post the full transcript.")
    async def leave(self, ctx: discord.ApplicationContext):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server.", ephemeral=True)
            return
        state = self.sessions.get(ctx.guild.id)
        vc = state["vc"] if state else ctx.guild.voice_client
        if vc is None:
            self.sessions.pop(ctx.guild.id, None)
            await ctx.respond("I'm not live right now.", ephemeral=True)
            return
        await ctx.respond("Stopping live transcription…")
        try:
            vc.stop_recording()  # triggers _on_finish
        except Exception as exc:
            # Session already dead (dropped connection, kicked, stale state) —
            # force cleanup so the bot never gets stuck.
            state = self.sessions.pop(ctx.guild.id, None)
            if state:
                for key in ("watchdog", "consumer"):
                    task = state.get(key)
                    if task:
                        task.cancel()
                self.storage.end_session(state["session_id"])
            try:
                await vc.disconnect(force=True)
            finally:
                await ctx.send_followup(
                    f"The live session had already stopped (`{exc}`); disconnected and cleaned up."
                )

    @discord.slash_command(name="ping", description="Check that the bot is responsive.")
    async def ping(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"Pong! ({round(self.bot.latency * 1000)}ms)")

    @discord.slash_command(name="translate",
                           description="Live-translate streamed lines into a language ('off' to disable).")
    async def translate(self, ctx: discord.ApplicationContext,
                        language: discord.Option(str, "Target language (e.g. 'en', 'English', 'Spanish') or 'off'"),
                        channel: discord.Option(discord.TextChannel, "Where translated lines go (default: here)",
                                                required=False) = None):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server.", ephemeral=True)
            return
        lang = language.strip()
        if lang.lower() in ("off", "none", "disable", "disabled"):
            self.storage.set_setting(ctx.guild.id, "translate_lang", "")
            state = self.sessions.get(ctx.guild.id)
            if state:
                state["translate_lang"] = None
            await ctx.respond("Live translation **disabled**.")
            return
        dest = channel or ctx.channel
        self.storage.set_setting(ctx.guild.id, "translate_lang", lang)
        self.storage.set_setting(ctx.guild.id, "translate_channel_id", dest.id)
        state = self.sessions.get(ctx.guild.id)
        if state:
            state["translate_lang"] = lang
            state["translate_channel"] = dest
        note = ""
        if not getattr(self.llm, "available", False):
            note = ("\n⚠️ No LLM is configured, so nothing will be translated yet — set "
                    "`RESONODE_LLM=openai|anthropic` and the matching API key in `.env`.")
        await ctx.respond(f"Live translation → **{lang}** in {dest.mention}.{note}")

    @discord.slash_command(name="moderation",
                           description="Toggle AI voice-moderation alerts for streamed audio.")
    async def moderation(self, ctx: discord.ApplicationContext,
                         enabled: discord.Option(str, "Turn moderation alerts on or off",
                                                 choices=["on", "off"]),
                         channel: discord.Option(discord.TextChannel, "Where alerts go (default: here)",
                                                 required=False) = None):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server.", ephemeral=True)
            return
        if enabled == "off":
            self.storage.set_setting(ctx.guild.id, "moderation_on", "")
            state = self.sessions.get(ctx.guild.id)
            if state:
                state["moderate"] = False
            await ctx.respond("Voice-moderation alerts **disabled**.")
            return
        dest = channel or ctx.channel
        self.storage.set_setting(ctx.guild.id, "moderation_on", "1")
        self.storage.set_setting(ctx.guild.id, "moderation_channel_id", dest.id)
        state = self.sessions.get(ctx.guild.id)
        if state:
            state["moderate"] = True
            state["moderation_channel"] = dest
        note = ""
        if not getattr(self.llm, "available", False):
            note = ("\n⚠️ No LLM is configured, so nothing will be checked yet — set "
                    "`RESONODE_LLM=openai|anthropic` and the matching API key in `.env`.")
        await ctx.respond(f"Voice-moderation alerts **enabled** → {dest.mention}.{note}")

    def _speaker_name(self, guild, uid):
        member = guild.get_member(uid)
        if member is not None:
            return member.display_name
        user = self.bot.get_user(uid)
        return user.display_name if user is not None else f"User {uid}"

    async def _transcribe_and_emit(self, state, uid, pcm):
        try:
            segs = await self.stt.transcribe_pcm(pcm, STREAM_RATE, STREAM_CHANNELS, STREAM_WIDTH)
        except Exception as exc:
            print(f"[resonode] streaming STT error: {exc}")
            return
        text = " ".join(s.text for s in segs).strip()
        if not text:
            return
        name = self._speaker_name(state["guild"], uid)
        t = time.monotonic() - state["start"]
        try:
            self.storage.add_utterance(
                state["session_id"], state["guild"].id,
                Utterance(speaker_name=name, speaker_id=uid, start_s=t, end_s=t,
                          text=text, lang=(segs[0].lang if segs else None)),
            )
        except Exception:
            pass
        try:
            await state["channel"].send(f"`[{format_timestamp(t)}]` **{name}:** {text}")
        except Exception:
            pass
        await asyncio.gather(
            self._maybe_translate(state, name, t, text, segs),
            self._maybe_moderate(state, name, t, text),
        )

    async def _maybe_moderate(self, state, name, t, text):
        """Alert the mod channel when the LLM flags a streamed utterance."""
        if not state.get("moderate") or not getattr(self.llm, "available", False):
            return
        try:
            verdict = await asyncio.to_thread(self.llm.moderate, text)
        except Exception as exc:
            print(f"[resonode] moderation error: {exc}")
            return
        if not verdict.get("flagged"):
            return
        chan = state.get("moderation_channel") or state["channel"]
        reason = (verdict.get("reason") or "").removeprefix("FLAG:").strip()
        try:
            await chan.send(
                f"⚠️ **Voice moderation** `[{format_timestamp(t)}]` **{name}:** {text[:500]}\n"
                f"> {reason[:800]}"
            )
        except Exception:
            pass

    async def _maybe_translate(self, state, name, t, text, segs):
        """Post a translated line when live translation is enabled and an LLM is set."""
        lang = state.get("translate_lang")
        if not lang or not getattr(self.llm, "available", False):
            return
        detected = (segs[0].lang or "") if segs else ""
        if detected and detected.lower() == lang.lower():
            return  # already in the target language (exact match on ISO codes)
        try:
            translated = await asyncio.to_thread(self.llm.translate, text, lang)
        except Exception as exc:
            print(f"[resonode] translation error: {exc}")
            return
        if not translated:
            return
        chan = state.get("translate_channel") or state["channel"]
        try:
            await chan.send(f"🌐 `[{format_timestamp(t)}]` **{name}:** {translated[:1800]}")
        except Exception:
            pass

    async def _consume(self, guild_id):
        state = self.sessions.get(guild_id)
        if not state:
            return
        q = state["queue"]
        try:
            while True:
                uid, pcm = await q.get()
                await self._transcribe_and_emit(state, uid, pcm)
        except asyncio.CancelledError:
            pass

    async def _watchdog(self, guild_id):
        try:
            while guild_id in self.sessions:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                state = self.sessions.get(guild_id)
                if not state:
                    break
                for uid, pcm in state["sink"].flush_idle(self.idle_flush_ms):
                    state["queue"].put_nowait((uid, pcm))
        except asyncio.CancelledError:
            pass

    async def _on_finish(self, sink, channel):
        guild = channel.guild
        state = self.sessions.get(guild.id)
        try:
            await sink.vc.disconnect(force=True)
        except Exception:
            pass
        if not state:
            return
        for key in ("watchdog", "consumer"):
            task = state.get(key)
            if task:
                task.cancel()
        q = state["queue"]
        while not q.empty():
            uid, pcm = q.get_nowait()
            await self._transcribe_and_emit(state, uid, pcm)
        for uid, pcm in sink.flush_all():
            await self._transcribe_and_emit(state, uid, pcm)

        self.storage.end_session(state["session_id"])
        rows = self.storage.session_utterances(state["session_id"])
        self.sessions.pop(guild.id, None)
        if not rows:
            await channel.send("Live session ended — no speech was transcribed.")
            return
        header = (
            f"Live transcript for {guild.name}\n"
            f"Recorded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Model: {self.config.whisper_model}\n" + "=" * 60
        )
        text = render_transcript(rows, header)
        await channel.send(
            content="Live session ended. Full transcript:",
            file=discord.File(io.BytesIO(text.encode("utf-8")),
                              filename=f"transcript_{state['session_id']}.txt"),
        )
        if self.config.auto_summary and getattr(self.llm, "available", False):
            try:
                summary = await asyncio.to_thread(self.llm.summarize, text)
                if summary:
                    await channel.send("**Session summary**\n" + summary[:1900])
            except Exception as exc:
                print(f"[resonode] auto-summary error: {exc}")
