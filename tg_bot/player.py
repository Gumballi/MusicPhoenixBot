"""PyTgCalls worker with per-chat state and auto-advancing queue.

The spare-account *userbot* (STRING_SESSION) owns the voice chat -- the main bot
never gets VC powers.  Each group chat gets an isolated playback state (chat id
-> ChatState): its own queue, its own worker task, its own PyTgCalls group-call.
Nothing is global single-queue, so group A can never hijack group B's stream.

Stream-end is detected through PyTgCalls and pops the next queue item
automatically (no dead-silence after a track finishes).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from tg_bot.config import LOGGER
from tg_bot.resolver import resolve_track

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream, AudioQuality
    from pytgcalls.types.update import Update

    # py-tgcalls 2.3.3 has NO StreamEnded / StreamAudioEnded class to import.
    # Never guess the class name again: detect end-of-stream dynamically from
    # the update's runtime class/status so this module cannot ImportError on boot.
    PYTGCALLS_READY = True
except Exception:
    PyTgCalls = None
    MediaStream = None
    AudioQuality = None
    Update = None
    PYTGCALLS_READY = False
    LOGGER.exception("PyTgCalls import failed (sidecar will refuse streaming).")


@dataclass
class QueueItem:
    title: str
    url: str
    requester: Optional[int]
    webpage: Optional[str]
    requested_by: Optional[str] = None


@dataclass
class ChatState:
    """Playback state isolated per voice chat."""

    chat_id: int
    queue: Deque[QueueItem] = field(default_factory=deque)
    task: Optional[asyncio.Task] = None
    current: Optional[QueueItem] = None
    playing: bool = False
    paused: bool = False
    advance: asyncio.Event = field(default_factory=asyncio.Event)


class VirtualAudioClient:
    """PyTgCalls wrapper that keeps ALL PyTgCalls surfaces in one place.

    PyTgCalls 2.x exposes play/pause/resume/end_stream on the client itself;
    stream-end arrives through `on_update` with a StreamEnded object.  Gate the
    whole adapter behind PYTGCALLS_READY so import noise can't crash boot.
    """

    def __init__(self, app) -> None:
        if not PYTGCALLS_READY:
            raise RuntimeError("PyTgCalls is not importable on this host")
        self._call = PyTgCalls(app)

    def on_update(self, *args: Any, **kwargs: Any):
        return self._call.on_update(*args, **kwargs)

    async def start(self) -> None:
        await self._call.start()

    async def play(self, chat_id: int, url: str) -> None:
        stream = MediaStream(url, audio_parameters=AudioQuality.HIGH)
        await self._call.play(chat_id, stream)

    async def pause(self, chat_id: int) -> None:
        await self._call.pause_stream(chat_id)

    async def resume(self, chat_id: int) -> None:
        await self._call.resume_stream(chat_id)

    async def skip(self, chat_id: int) -> None:
        await self._call.end_stream(chat_id)

    async def leave(self, chat_id: int) -> None:
        await self._call.leave_group_call(chat_id)


class MusicPlayer:
    """Per-chat queue + worker.  Call resolve() yourself, then add()/play()."""

    def __init__(self, app) -> None:
        self.app = app
        try:
            self.vc = VirtualAudioClient(app)
        except RuntimeError:
            self.vc = None
        self.states: dict[int, ChatState] = {}

    # -- state helpers ------------------------------------------------------

    def _state(self, chat_id: int) -> ChatState:
        state = self.states.get(chat_id)
        if state is None:
            state = ChatState(chat_id=chat_id)
            self.states[chat_id] = state
            state.advance.set()
        return state

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self.vc is None:
            LOGGER.critical("PyTgCalls unavailable -> VC worker offline.")
            return
        self.vc.on_update(self._on_update)
        await self.vc.start()

    async def _on_update(self, _, update: Update) -> None:
        """Triggered by PyTgCalls on every stream update; advance on any end.

        Poke's rule: never isinstance() on a class you cannot import.  pytgcalls
        2.3.3 reports end-of-stream dynamically (a Stream***d status/type on the
        update OBJECT), so we inspect the object's runtime attributes instead of
        importing a StreamEnded/StreamAudioEnded name that does not exist there.
        """
        if update is None:
            return

        # 1) dynamic type name (exact equality only -- NEVER substring, or
        #    periodic VC keepalive/status echoes with "StreamAudioEnded"-ish
        #    strings embedded would false-advance the queue at ~20s)
        type_name = type(update).__name__
        ended = type_name in ("StreamAudioEnded", "StreamEnded", "StreamAudioEnd")
        if not ended:
            # 2) exact status token (whole-value match, not substring)
            status = str(getattr(update, "status", ""))
            ended = status.upper() in ("STREAM_ENDED", "STREAM_AUDIO_ENDED", "ENDED")
        if not ended:
            return

        chat_id = getattr(update, "chat_id", None) or getattr(update, "chat", None)
        if chat_id is None:
            return
        chat_id = int(chat_id)
        state = self.states.get(chat_id)
        if state is not None:
            await self.play_next(chat_id)

    # -- public API ---------------------------------------------------------

    async def add(self, query: str, requester: Optional[int] = None) -> QueueItem:
        """Resolve (in a worker thread -- yt-dlp is sync + gated) into a QueueItem."""
        info = await asyncio.to_thread(resolve_track, query)
        return QueueItem(
            title=info["title"],
            url=info["url"],
            requester=requester,
            webpage=info.get("webpage"),
        )

    async def play(self, chat_id: int, item: QueueItem) -> None:
        """Enqueue then start/keep the per-chat worker streaming."""
        if self.vc is None:
            raise RuntimeError("Voice-chat worker (PyTgCalls) is offline")
        state = self._state(chat_id)
        state.queue.append(item)
        state.advance.set()
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._worker(chat_id))

    async def _worker(self, chat_id: int) -> None:
        state = self._state(chat_id)
        while True:
            state.advance.clear()
            if not state.queue:
                if state.current:
                    await self._stop_current(chat_id)
                state.playing = False
                state.current = None
                break
            item = state.queue.popleft()
            state.current = item
            state.playing = True
            state.paused = False
            try:
                await self.vc.play(chat_id, item.url)
                LOGGER.info("chat %s now streaming %r", chat_id, item.title)
            except Exception as exc:
                state.current = None
                state.playing = False
                LOGGER.exception("chat %s stream play failed: %s", chat_id, exc)
                await self.vc.leave(chat_id)
                break
            await state.advance.wait()

    async def play_next(self, chat_id: int) -> None:
        state = self._state(chat_id)
        state.advance.set()
        if state.task is not None and not state.task.done():
            return
        if state.queue:
            state.task = asyncio.create_task(self._worker(chat_id))

    async def _stop_current(self, chat_id: int) -> None:
        try:
            await self.vc.leave(chat_id)
        except Exception:
            pass

    async def pause(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        if not state.playing:
            return False
        try:
            await self.vc.pause(chat_id)
            state.paused = True
            return True
        except Exception:
            LOGGER.exception("pause failed chat %s", chat_id)
            return False

    async def resume(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        if not state.playing:
            return False
        try:
            await self.vc.resume(chat_id)
            state.paused = False
            return True
        except Exception:
            LOGGER.exception("resume failed chat %s", chat_id)
            return False

    async def skip(self, chat_id: int) -> Optional[QueueItem]:
        state = self._state(chat_id)
        bumped = state.current
        try:
            await self.vc.skip(chat_id)
        except Exception:
            LOGGER.exception("skip failed chat %s", chat_id)
        state.advance.set()
        if state.task is not None and not state.task.done():
            state.task.cancel()
        if state.queue:
            state.task = asyncio.create_task(self._worker(chat_id))
        else:
            state.current = None
            state.playing = False
        return bumped

    async def stop(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        was = state.playing or bool(state.queue)
        state.queue.clear()
        state.current = None
        state.playing = False
        if state.task is not None:
            state.task.cancel()
            state.task = None
        try:
            await self.vc.leave(chat_id)
        except Exception:
            LOGGER.exception("leave failed chat %s", chat_id)
        return was

    def now_playing(self, chat_id: int) -> Optional[QueueItem]:
        state = self.states.get(chat_id)
        return state.current if state else None

    def queue_list(self, chat_id: int) -> list[QueueItem]:
        state = self.states.get(chat_id)
        return list(state.queue) if state else []
