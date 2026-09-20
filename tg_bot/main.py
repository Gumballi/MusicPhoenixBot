"""
Phoenix Music sidecar - dual-client architecture
bot listens for commands, assistant (userbot) streams audio via PyTgCalls
"""

import asyncio
import logging

from pyrogram import idle, Client, filters
from pyrogram.types import Message

from tg_bot.config import (
    API_ID,
    API_HASH,
    STRING_SESSION,
    BOT_TOKEN,
    ADMIN_IDS,
    LOGGER,
)

from tg_bot.health import start_health_server
from tg_bot.player import MusicPlayer

from tg_bot import commands

logging.basicConfig(
    level=LOGGER.level,
    format="%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s",
)

# NOTE: bot/assistant/player are constructed inside _boot() on purpose.  Both
# Pyrogram and PyTgCalls bind their dispatchers/loops to whatever asyncio loop
# is active at INSTANTIATION.  Module-level construction grabs the loop that
# happened to exist at import time; asyncio.run(_boot()) then spins up a fresh
# loop and Render dies with RuntimeError: Future attached to a different loop.


async def _boot() -> None:
    start_health_server()
    bot = Client(
        "music_phoenix_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    )
    assistant = Client(
        "music_phoenix_assistant",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=STRING_SESSION,
        in_memory=True,
        # MUST receive updates: Telegram drops VC calls after ~10-20s if the
        # participant userbot never answers group-call heartbeats.
        no_updates=False,
    )
    player = MusicPlayer(assistant)

    LOGGER.info("Starting Bot and Assistant...")
    await bot.start()
    await assistant.start()
    await player.start()
    commands.register(bot, player)
    LOGGER.info("Music Phoenix is online and ready for /play in groups!")
    await idle()
    await bot.stop()
    await assistant.stop()


if __name__ == "__main__":
    asyncio.run(_boot())
