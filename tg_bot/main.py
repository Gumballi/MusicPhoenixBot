"""
Phoenix Music sidecar - Pyrogram *userbot* that streams audio into a group
voice chat using PyTgCalls.  Poke's architecture: the spare account does the
VC work, so the main (PTB) bot never needs voice-chat powers.

Boot flow (Render):  python main.py
"""

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from tg_bot.config import (
    API_ID,
    API_HASH,
    STRING_SESSION,
    ADMIN_IDS,
    LOGGER,
)

from tg_bot.resolver import resolve_track, ResolveError   # yt-dlp (non-YouTube)
from tg_bot.player import MusicPlayer

logging.basicConfig(
    level=LOGGER.level,
    format="%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s",
)


app = Client(
    "music_phoenix",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION,
    in_memory=True,
    no_updates=False,  # userbot must receive group messages (voice commands)
)


player = MusicPlayer(app)
QUEUE_EMPTY = asyncio.Event()
QUEUE_EMPTY.set()


async def _boot() -> None:
    """Render entrypoint: start the spare-account userbot, then the VC player."""
    LOGGER.info("Phoenix sidecar waking up (spare account userbot)...")
    await app.start()
    await player.start()
    LOGGER.info("PyTgCalls sidecar online; waiting for /play in a group.")
    await app.idle()


if __name__ == "__main__":
    asyncio.run(_boot())
