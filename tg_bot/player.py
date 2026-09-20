"""MusicPlayer: the PyTgCalls sidecar streamer.

The sidecar-architecture lesson (a datacenter-safe player never streams a
gated YouTube URL): this userbot account does ALL the voice-chat work, and
the main PTB bot stays free of voice-chat powers.

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
    from pytgcalls.types import MediaStream, AudioQuality
    from pytgcalls.types import MediaStream, AudioQuality
    if not hasattr(MediaStream, "__name__"):
        raise RuntimeError("pytgcalls.types.MediaStream missing")
    PYTGCALLS_READY = True
except ImportError:
    PyTgCalls = None
    MediaStream = None
    AudioQuality = None
    PYTGCALLS_READY = False
    LOGGER.exception(
        "pytgcalls import FAILED (not 'not installed') — full traceback above: "
        "the true error Render hides from you, shown instead of a lie."
    )
except Exception:
    PyTgCalls = None
    MediaStream = None
    AudioQuality = None
    PYTGCALLS_READY = False
    LOGGER.exception(
        "pytgcalls raised a runtime error at import (NOT missing) — traceback above."
    )


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
