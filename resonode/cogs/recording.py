"""Recording cog — the /join, /leave, /ping batch flow.

Records a voice channel (one WaveSink per speaker), transcribes each speaker via
the configured STT backend, persists every line to storage, then posts the
transcript file. This is the batch path; the streaming path reuses the same STT
+ storage seam (see resonode/audio.py).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

from ..storage import Utterance
from ..transcripts import render_transcript


class RecordingCog(commands.Cog):
    def __init__(self, bot, config, stt, storage, llm=None):
        self.bot = bot
        self.config = config
        self.stt = stt
        self.storage = storage
        self.llm = llm
        self.active: dict[int, dict] = {}  # guild_id -> {vc, session_id, channel}
        self.rec_dir = Path(config.recordings_dir)
        self.tx_dir = Path(config.transcripts_dir)
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.tx_dir.mkdir(parents=True, exist_ok=True)

    @discord.slash_command(name="join", description="Join your voice channel and start recording.")
    async def join(self, ctx: discord.ApplicationContext):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server voice channel.", ephemeral=True)
            return
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.respond("You need to be in a voice channel first.", ephemeral=True)
            return
        if ctx.guild.id in self.active:
            await ctx.respond("I'm already recording in this server. Use `/leave` to stop.", ephemeral=True)
            return
        channel = ctx.author.voice.channel
        await ctx.defer()
        try:
            vc = await channel.connect()
        except discord.ClientException:
            await ctx.respond("I'm already connected to a voice channel here.")
            return
        session_id = self.storage.start_session(
            ctx.guild.id, ctx.guild.name, channel.id, channel.name, self.config.whisper_model
        )
        self.active[ctx.guild.id] = {"vc": vc, "session_id": session_id, "channel": ctx.channel}
        vc.start_recording(discord.sinks.WaveSink(), self._on_finish, ctx.channel)
        await ctx.respond(
            f"Joined **{channel.name}** and started recording. Use `/leave` when you're done.\n"
            f"-# 💡 Best accuracy: set your Discord **Input Profile** to **Voice Isolation** "
            f"(User Settings → Voice & Video) for cleaner transcripts."
        )

    @discord.slash_command(name="leave", description="Stop recording and post the transcript.")
    async def leave(self, ctx: discord.ApplicationContext):
        if ctx.guild is None:
            await ctx.respond("Please use this in a server.", ephemeral=True)
            return
        state = self.active.get(ctx.guild.id)
        vc = state["vc"] if state else ctx.guild.voice_client
        if vc is None:
            self.active.pop(ctx.guild.id, None)
            await ctx.respond("I'm not recording anything right now.", ephemeral=True)
            return
        await ctx.respond("Leaving the channel and starting transcription...")
        try:
            vc.stop_recording()  # triggers _on_finish
        except Exception as exc:
            # Recording already dead (dropped connection, kicked, stale state) —
            # force-disconnect so the bot never gets stuck in the channel.
            self.active.pop(ctx.guild.id, None)
            try:
                await vc.disconnect(force=True)
            finally:
                await ctx.send_followup(
                    f"The recording had already stopped (`{exc}`), so I just disconnected."
                )

    @discord.slash_command(name="ping", description="Check that the bot is responsive.")
    async def ping(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"Pong! ({round(self.bot.latency * 1000)}ms)")

    async def _on_finish(self, sink: "discord.sinks.WaveSink", channel: discord.TextChannel):
        guild = channel.guild
        state = self.active.pop(guild.id, None)
        session_id = state["session_id"] if state else None
        try:
            await sink.vc.disconnect(force=True)
        except Exception as exc:
            print(f"[resonode] disconnect after recording failed: {exc}")

        if not sink.audio_data:
            await channel.send("No audio was captured, so there's nothing to transcribe.")
            if session_id:
                self.storage.end_session(session_id)
            return

        status = await channel.send(
            f"Recording stopped. Transcribing {len(sink.audio_data)} speaker(s) "
            f"with the **{self.config.whisper_model}** model — this can take a moment..."
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        any_text = False

        for speaker, audio in sink.audio_data.items():
            # py-cord >= 2.9 keys audio_data by Member/User; older versions use int ids.
            user_id = speaker if isinstance(speaker, int) else speaker.id
            member = guild.get_member(user_id) or (None if isinstance(speaker, int) else speaker)
            if member is not None:
                name = member.display_name
            else:
                user = self.bot.get_user(user_id)
                name = user.display_name if user is not None else f"User {user_id}"

            wav_path = self.rec_dir / f"{stamp}_{user_id}.wav"
            audio.file.seek(0)
            wav_path.write_bytes(audio.file.read())
            try:
                segments = await self.stt.transcribe_file(str(wav_path))
            except Exception as exc:  # keep one bad speaker from sinking the run
                await channel.send(f"Failed to transcribe audio from **{name}**: {exc}")
                segments = []
            finally:
                wav_path.unlink(missing_ok=True)

            for seg in segments:
                any_text = True
                if session_id:
                    self.storage.add_utterance(
                        session_id, guild.id,
                        Utterance(speaker_name=name, speaker_id=user_id,
                                  start_s=seg.start, end_s=seg.end, text=seg.text, lang=seg.lang),
                    )

        if session_id:
            self.storage.end_session(session_id)
        if not any_text:
            await status.edit(content="Transcription produced no text (was anyone talking?).")
            return

        rows = self.storage.session_utterances(session_id) if session_id else []
        header = (
            f"Transcript for voice session in {guild.name}\n"
            f"Recorded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Model: {self.config.whisper_model}\n" + "=" * 60
        )
        text = render_transcript(rows, header)
        out = self.tx_dir / f"transcript_{guild.id}_{stamp}.txt"
        out.write_text(text, encoding="utf-8")

        await status.edit(content="Transcription complete! Here's the transcript:")
        await channel.send(file=discord.File(str(out)))
        await self._maybe_auto_summary(channel, text)

    async def _maybe_auto_summary(self, channel, transcript_text):
        """Post an LLM session summary when RESONODE_AUTO_SUMMARY is on."""
        if not self.config.auto_summary or not getattr(self.llm, "available", False):
            return
        try:
            summary = await asyncio.to_thread(self.llm.summarize, transcript_text)
            if summary:
                await channel.send("**Session summary**\n" + summary[:1900])
        except Exception as exc:
            print(f"[resonode] auto-summary error: {exc}")
