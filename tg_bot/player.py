"""PyTgCalls worker with per-chat state and auto-advancing queue."""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from tg_bot.config import LOGGER
from tg_bot.resolver import resolve_track

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream, AudioQuality, StreamEnded, ChatUpdate

    PYTGCALLS_READY = True
except Exception:
    PyTgCalls = MediaStream = AudioQuality = StreamEnded = ChatUpdate = None
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
    chat_id: int
    queue: Deque[QueueItem] = field(default_factory=deque)
    task: Optional[asyncio.Task] = None
    current: Optional[QueueItem] = None
    playing: bool = False
    paused: bool = False
    advance: asyncio.Event = field(default_factory=asyncio.Event)


class VirtualAudioClient:
    def __init__(self, app) -> None:
        if not PYTGCALLS_READY:
            raise RuntimeError("PyTgCalls is not importable on this host")
        self._call = PyTgCalls(app)

    def add_handler(self, func: Any) -> None:
        # FIX: on_update() is a DECORATOR FACTORY. It must be called with no
        # args to get the decorator, then applied to the handler. The old
        # `on_update(self._on_update)` bound the handler to the `filters`
        # parameter and discarded the decorator -> handler never registered.
        self._call.on_update()(func)

    async def start(self) -> None:
        await self._call.start()

    async def play(self, chat_id: int, path: str) -> None:
        stream = MediaStream(
            path,
            audio_parameters=AudioQuality.HIGH,
            # FIX: never let AUTO_DETECT open a video source in a group call.
            video_flags=MediaStream.Flags.IGNORE,
            audio_flags=MediaStream.Flags.REQUIRED,
        )
        await self._call.play(chat_id, stream)

    async def pause(self, chat_id: int) -> None:
        await self._call.pause_stream(chat_id)

    async def resume(self, chat_id: int) -> None:
        await self._call.resume_stream(chat_id)

    async def leave(self, chat_id: int) -> None:
        await self._call.leave_group_call(chat_id)


class MusicPlayer:
    def __init__(self, app) -> None:
        self.app = app
        try:
            self.vc = VirtualAudioClient(app)
        except RuntimeError:
            self.vc = None
        self.states: dict[int, ChatState] = {}

    def _state(self, chat_id: int) -> ChatState:
        state = self.states.get(chat_id)
        if state is None:
            # FIX: do NOT pre-set advance here. A set event makes the worker
            # skip its first wait and tear straight through the queue.
            state = ChatState(chat_id=chat_id)
            self.states[chat_id] = state
        return state

    async def start(self) -> None:
        if self.vc is None:
            LOGGER.critical("PyTgCalls unavailable -> VC worker offline.")
            return
        self.vc.add_handler(self._on_update)
        await self.vc.start()

    async def _on_update(self, _client, update) -> None:
        """Handlers are propagated as func(client, update)."""
        if isinstance(update, StreamEnded):
            if not (update.stream_type & StreamEnded.Type.AUDIO):
                return
            LOGGER.info("chat %s stream ended -> advancing", update.chat_id)
            state = self.states.get(int(update.chat_id))
            if state is not None:
                state.advance.set()
            return

        if ChatUpdate is not None and isinstance(update, ChatUpdate):
            if update.status & (
                ChatUpdate.Status.LEFT_CALL | ChatUpdate.Status.KICKED
            ):
                LOGGER.warning("chat %s: assistant left/kicked", update.chat_id)
                await self.stop(int(update.chat_id))

    async def add(self, query: str, requester: Optional[int] = None) -> QueueItem:
        info = await asyncio.to_thread(resolve_track, query)
        return QueueItem(
            title=info["title"],
            url=info["url"],
            requester=requester,
            webpage=info.get("webpage"),
        )

    async def play(self, chat_id: int, item: QueueItem) -> None:
        if self.vc is None:
            raise RuntimeError("Voice-chat worker (PyTgCalls) is offline")
        state = self._state(chat_id)
        state.queue.append(item)
        # FIX: no advance.set() here. The old code woke the worker mid-track,
        # so a second /play cut the first song off instead of queueing it.
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._worker(chat_id))

    async def _worker(self, chat_id: int) -> None:
        state = self._state(chat_id)
        try:
            while state.queue:
                item = state.queue.popleft()
                state.current = item
                state.playing = True
                state.paused = False
                state.advance.clear()
                try:
                    await self.vc.play(chat_id, item.url)
                    LOGGER.info("chat %s now streaming %r", chat_id, item.title)
                except Exception:
                    # One bad track must not kill the whole queue.
                    LOGGER.exception("chat %s: play failed for %r", chat_id, item.title)
                    self._discard(item)
                    continue
                await state.advance.wait()
                self._discard(item)
        except asyncio.CancelledError:
            raise
        finally:
            state.current = None
            state.playing = False
            if not state.queue:
                await self._stop_current(chat_id)

    @staticmethod
    def _discard(item: QueueItem) -> None:
        """Delete the /tmp download so Render's disk doesn't fill up."""
        try:
            if item.url.startswith("/tmp/") and os.path.exists(item.url):
                os.remove(item.url)
        except OSError:
            LOGGER.debug("could not remove %s", item.url)

    async def _stop_current(self, chat_id: int) -> None:
        try:
            await self.vc.leave(chat_id)
        except Exception:
            pass

    async def pause(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        if not state.playing or state.paused:
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
        if not state.playing or not state.paused:
            return False
        try:
            await self.vc.resume(chat_id)
            state.paused = False
            return True
        except Exception:
            LOGGER.exception("resume failed chat %s", chat_id)
            return False

    async def skip(self, chat_id: int) -> Optional[QueueItem]:
        # FIX: don't cancel the worker and don't call end_stream. PyTgCalls.play
        # on an already-active call swaps the source in place, so just release
        # the worker and let it advance.
        state = self._state(chat_id)
        if not state.playing:
            return None
        bumped = state.current
        state.advance.set()
        return bumped

    async def stop(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        was = state.playing or bool(state.queue)
        for item in list(state.queue):
            self._discard(item)
        state.queue.clear()
        if state.current is not None:
            self._discard(state.current)
        state.current = None
        state.playing = False
        state.advance.set()
        task, state.task = state.task, None
        if task is not None and not task.done():
            task.cancel()
        try:
            await self.vc.leave(chat_id)
        except Exception:
            LOGGER.debug("leave failed chat %s (probably not in call)", chat_id)
        return was

    def now_playing(self, chat_id: int) -> Optional[QueueItem]:
        state = self.states.get(chat_id)
        return state.current if state else None

    def queue_list(self, chat_id: int) -> list[QueueItem]:
        state = self.states.get(chat_id)
        return list(state.queue) if state else []
