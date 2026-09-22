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
    duration: Optional[int] = None


@dataclass
class ChatState:
    chat_id: int
    queue: Deque[QueueItem] = field(default_factory=deque)
    task: Optional[asyncio.Task] = None
    current: Optional[QueueItem] = None
    playing: bool = False
    paused: bool = False
    advance: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    card_id: Optional[int] = None
    card_item: Optional[QueueItem] = None


class VirtualAudioClient:
    def __init__(self, app) -> None:
        if not PYTGCALLS_READY:
            raise RuntimeError("PyTgCalls is not importable on this host")
        self._call = PyTgCalls(app)

    def add_handler(self, func: Any) -> None:
        self._call.on_update()(func)

    async def start(self) -> None:
        await self._call.start()

    async def play(self, chat_id: int, path: str) -> None:
        stream = MediaStream(
            path,
            audio_parameters=AudioQuality.HIGH,
            video_flags=MediaStream.Flags.IGNORE,
            audio_flags=MediaStream.Flags.REQUIRED,
        )
        await self._call.play(chat_id, stream)

    async def pause(self, chat_id: int) -> None:
        await self._call.pause(chat_id)

    async def resume(self, chat_id: int) -> None:
        await self._call.resume(chat_id)

    async def leave(self, chat_id: int) -> None:
        await self._call.leave_call(chat_id)


class MusicPlayer:
    def __init__(self, app, bot=None) -> None:
        self.app = app
        self.bot = bot
        try:
            self.vc = VirtualAudioClient(app)
        except RuntimeError:
            self.vc = None
        self.states: dict[int, ChatState] = {}

    def _state(self, chat_id: int) -> ChatState:
        state = self.states.get(chat_id)
        if state is None:
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
            duration=info.get("duration"),
        )

    async def add_resolved(self, info: dict, requester: Optional[int] = None) -> QueueItem:
        """Enqueue an already-resolved track dict (from the search picker)."""
        return QueueItem(
            title=info["title"],
            url=info["url"],
            requester=requester,
            webpage=info.get("webpage"),
            duration=info.get("duration"),
        )

    async def play(self, chat_id: int, item: QueueItem) -> None:
        if self.vc is None:
            raise RuntimeError("Voice-chat worker (PyTgCalls) is offline")
        state = self._state(chat_id)
        async with state.lock:
            state.queue.append(item)
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
                    if state.card_id is not None and state.card_item is not item:
                        await self.delete_card(chat_id)
                    size = (
                        os.path.getsize(item.url)
                        if item.url.startswith("/tmp/") and os.path.isfile(item.url)
                        else None
                    )
                    LOGGER.info(
                        "chat %s: handing %r to VC (expected %ss, file=%s bytes)",
                        chat_id,
                        item.title,
                        item.duration if item.duration is not None else "?",
                        size if size is not None else "remote-url",
                    )
                    await self.vc.play(chat_id, item.url)
                    LOGGER.info("chat %s now streaming %r", chat_id, item.title)
                except Exception:
                    LOGGER.exception("chat %s: play failed for %r", chat_id, item.title)
                    self._discard(item)
                    state.current = None
                    state.playing = False
                    continue
                await state.advance.wait()
                self._discard(item)
                state.current = None
        except asyncio.CancelledError:
            raise
        finally:
            if state.current is not None:
                self._discard(state.current)
            state.current = None
            state.playing = False
            if not state.queue:
                await self._stop_current(chat_id)
                await self.delete_card(chat_id)

    @staticmethod
    def _discard(item: QueueItem) -> None:
        """Delete a downloaded temporary file, if present."""
        try:
            if item.url.startswith("/tmp/") and os.path.isfile(item.url):
                os.remove(item.url)
        except OSError:
            LOGGER.debug("could not remove %s", item.url)

    async def _stop_current(self, chat_id: int) -> None:
        try:
            await self.vc.leave(chat_id)
            LOGGER.info("chat %s: left voice call", chat_id)
        except Exception as exc:
            LOGGER.warning("chat %s: leave_call failed: %s", chat_id, exc)

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
        state = self._state(chat_id)
        if not state.playing:
            return None
        bumped = state.current
        state.advance.set()
        return bumped

    async def stop(self, chat_id: int) -> bool:
        state = self._state(chat_id)
        async with state.lock:
            task = state.task
            was_active = (
                state.playing
                or bool(state.queue)
                or (task is not None and not task.done())
            )
            queued = list(state.queue)
            state.queue.clear()
            current = state.current
            state.current = None
            state.playing = False
            state.paused = False
            state.advance.set()
            state.task = None

        for item in queued:
            self._discard(item)
        if current is not None:
            self._discard(current)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            await self.vc.leave(chat_id)
        except Exception:
            LOGGER.debug("leave failed chat %s (probably not in call)", chat_id)
        return was_active

    def now_playing(self, chat_id: int) -> Optional[QueueItem]:
        state = self.states.get(chat_id)
        return state.current if state else None

    def queue_list(self, chat_id: int) -> list[QueueItem]:
        state = self.states.get(chat_id)
        return list(state.queue) if state else []

    async def swap_card(self, chat_id: int, item: QueueItem, message) -> None:
        """Point the active now-playing card at a message, deleting the old one.

        Keeps at most one media card in the group: the previous now-playing
        message is removed the moment a new card takes over.
        """
        state = self._state(chat_id)
        old = state.card_id
        new_id = message.id if getattr(message, "id", None) else None
        state.card_id = new_id
        state.card_item = item
        if old and old != new_id and self.bot is not None:
            try:
                await self.bot.delete_messages(chat_id, old)
            except Exception:
                LOGGER.debug("could not delete previous card %s", old)

    async def delete_card(self, chat_id: int) -> None:
        """Delete and forget the active now-playing card (e.g. on /stop)."""
        state = self.states.get(chat_id)
        if state is not None and state.card_id is not None:
            msg_id = state.card_id
            state.card_id = None
            state.card_item = None
            if self.bot is not None:
                try:
                    await self.bot.delete_messages(chat_id, msg_id)
                except Exception:
                    LOGGER.debug("could not delete card %s", msg_id)
