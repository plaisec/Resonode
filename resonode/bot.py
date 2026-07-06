"""Resonode entrypoint — wires config, storage, and pluggable services into the
py-cord bot and runs it.
"""
from __future__ import annotations

import struct
import sys
import warnings
from pathlib import Path

import discord

# py-cord deprecates start_recording's *args, but its own AudioReader only
# invokes the finish callback when args are non-empty (reader.py:
# `if self.after and self.args:`), so we must keep passing the channel arg or
# /leave never posts transcripts. Silence just that warning until py-cord
# fixes the guard (pycord PR #3159 / 2.9.x).
warnings.filterwarnings(
    "ignore", message=r"'args' parameter is deprecated since 2\.7"
)
from dotenv import load_dotenv

from .config import Config
from .storage import Storage
from .stt import get_stt
from .services import get_llm, get_memory
from .cogs.recording import RecordingCog
from .cogs.query import QueryCog


def ensure_opus_loaded() -> None:
    """Load libopus so the bot can decode incoming voice (py-cord ships it on Windows)."""
    if discord.opus.is_loaded():
        return
    if sys.platform == "win32":
        arch = "x64" if struct.calcsize("P") * 8 == 64 else "x86"
        dll = Path(discord.__file__).parent / "bin" / f"libopus-0.{arch}.dll"
        if dll.exists():
            try:
                discord.opus.load_opus(str(dll))
            except Exception as exc:
                print(f"Could not load bundled opus ({dll}): {exc}")
    if not discord.opus.is_loaded():
        print("WARNING: Opus could not be loaded — voice recording will not work.")


def build_bot(config, storage, stt, llm, memory) -> discord.Bot:
    # Slash commands don't need the privileged Message Content intent.
    intents = discord.Intents.default()
    # Set RESONODE_DEV_GUILD to register commands instantly in one test server;
    # otherwise global commands are used (these can take up to ~1 hour to appear).
    debug_guilds = [config.dev_guild_id] if config.dev_guild_id else None
    bot = discord.Bot(intents=intents, debug_guilds=debug_guilds)
    mode = "streaming" if config.streaming else "batch"

    @bot.event
    async def on_ready():
        print(f"Resonode online as {bot.user} (id: {bot.user.id})")
        print(f"  mode={mode}  STT={stt.name}  LLM={llm.name}  Memory={memory.name}  "
              f"Opus={discord.opus.is_loaded()}")

    # Recording vs live streaming share the /join and /leave names, so load exactly one.
    if config.streaming:
        from .cogs.streaming import StreamingCog
        bot.add_cog(StreamingCog(bot, config, stt, storage, llm))
    else:
        bot.add_cog(RecordingCog(bot, config, stt, storage, llm))
    bot.add_cog(QueryCog(bot, config, storage, llm, memory))
    return bot


def main() -> None:
    load_dotenv()
    config = Config.from_env()
    if not config.token:
        raise SystemExit("No DISCORD_TOKEN found. Copy .env.example to .env and add your bot token.")
    ensure_opus_loaded()
    storage = Storage(config.db_path)
    stt = get_stt(config)
    llm = get_llm(config)
    memory = get_memory(config, storage)
    print(f"Resonode starting — mode={'streaming' if config.streaming else 'batch'}, "
          f"STT={stt.name}, LLM={llm.name}, model={config.whisper_model} on {config.whisper_device}")
    if config.auto_summary and not llm.available:
        print("NOTE: RESONODE_AUTO_SUMMARY=true but no LLM is configured — summaries will be skipped.")
    bot = build_bot(config, storage, stt, llm, memory)
    try:
        bot.run(config.token)
    finally:
        storage.close()


if __name__ == "__main__":
    main()
