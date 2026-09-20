"""MusicPlayer: the PyTgCalls sidecar streamer (Poke's architecture).

PyTgCalls runs as a *userbot sidecar* on the spare account -- the PTB main bot
never gets voice-chat powers; this Pyrogram userbot alone streams into the
group VC.  Audio URLs come in from tg_bot.resolver (already gated against
YouTube); we just feed them to ffmpeg via AudioPiped.
"""

import asyncio
import logging
from collections import deque
from typing import Optional

from tg_bot.config import LOGGER
from tg_bot.resolver import resolve_track, ResolveError

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import AudioPiped
    from pytgcalls.exceptions import NoActiveGroupCall
    PYTGCALLS_READY = True
except ImportError:
    PyTgCalls = None
    AudioPiped = None
    NoActiveGroupCall = None
    PYTGCALLS_READY = False
    LOGGER.warning("pytgcalls not installed; /play will not stream")


class QueueItem:
    __slots__ = ("title", "url", "requester", "webpage")

    def __init__(self, title, url, requester, webpage):
        self.title = title
        self.url = url
        self.requester = requester
        self.webpage = webpage


class MusicPlayer:
    def __init__(self, app):
        self.app = app
        self.player = None
        self.queue = deque()
        self.current = None
        self.paused = False
        self.vc_chat_id = None

    async def start(self):
        if not PYTGCALLS_READY:
            raise RuntimeError("pytgcalls unavailable")
        self.player = PyTgCalls(self.app)
        await self.player.start()

    async def add(self, query, requester):
        """Resolve + enqueue; returns the QueueItem for the /play reply."""
        info = resolve_track(query)
        item = QueueItem(info["title"], info["url"], requester, info.get("webpage"))
        self.queue.append(item)
        return item
