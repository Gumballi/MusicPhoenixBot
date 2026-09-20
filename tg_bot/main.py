"""Phoenix Music sidecar - the dual-client spare-account sidecar.

Architecture (the datacenter-safe sidecar lesson): a *spare-account Pyrogram
userbot* does ALL the voice-chat work via PyTgCalls (spare account alone has
VC powers -- the main bot never gets them, which is what keeps a music bot
datacenter-safe).  PyTgCalls runs as a *userbot sidecar* on the spare account;
the main bot only listens for commands and feeds the queue.

Boot flow (Render):  python main.py
"""

import asyncio
import logging

import pyrogram
from pyrogram import Client, filters
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
from tg_bot.resolver import resolve_track, ResolveError   # yt-dlp (non-YouTube)
from tg_bot.player import MusicPlayer

logging.basicConfig(
    level=LOGGER.level,
    format="%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s",
)


# 1) The Bot (the face of the project, listens for commands)
bot = Client(
    "music_phoenix_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    no_updates=False,
)


# 2) The Userbot Assistant (Spare Account) -> streams into Voice Chat via PyTgCalls
assistant = Client(
    "music_phoenix_assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION,
    in_memory=True,
    no_updates=False,
)


player = MusicPlayer(assistant)   # the spare account does all the VC work


app_bot = Client(
    "music_phoenix_main",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    no_updates=True,   # PTB-side main bot never needs voice-chat powers
)
