"""Full command surface for the music bot."""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from pyrogram import Client, filters
from pyrogram.types import Message

from tg_bot.config import ADMIN_IDS, LOGGER

if TYPE_CHECKING:
    from tg_bot.player import MusicPlayer

_VC_HINT = "Make sure an active voice chat is running and the assistant is in the group."


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _translate(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "no_active_group_call" in text or "active group call" in text:
        return "No voice chat is running in this group. Start one first, then retry."
    if "user_not_participant" in text or "not a participant" in text or "user not in" in text:
        return "The assistant account isn't in this group yet. Add it, then retry."
    if "permission" in text or "forbidden" in text or "not allowed" in text:
        return "The assistant is missing voice-chat permissions. " + _VC_HINT
    return text or "Something went wrong on the server side."


async def _is_admin(message: Message) -> bool:
    user = message.from_user
    if user is None or user.id in ADMIN_IDS:
        return True
    try:
        member = await message.chat.get_member(user.id)
    except Exception:
        return False
    return member.status.value in ("administrator", "creator")


def register(bot: Client, player: "MusicPlayer") -> None:
    @bot.on_message(filters.command(["start"], prefixes=["/", "!"]))
    async def start_handler(_: Client, message: Message) -> None:
        await message.reply_text("Hello! Use /play <song or link> to stream music.")

    @bot.on_message(filters.command(["help"], prefixes=["/", "!"]))
    async def help_handler(_: Client, message: Message) -> None:
        await message.reply_text("/play <song or link> · /queue · /pause · /resume · /skip or /next · /stop")

    @bot.on_message(filters.command(["queue"], prefixes=["/", "!"]))
    async def queue_handler(_: Client, message: Message) -> None:
        items = player.queue_list(message.chat.id)
        current = player.now_playing(message.chat.id)
        if not items and current is None:
            await message.reply_text("The queue is empty. Use /play to add a track.")
            return
        lines = []
        if current is not None:
            lines.append("▶ Now playing: " + _safe(current.title))
        lines.extend("{}. {}".format(i, _safe(item.title)) for i, item in enumerate(items, 1))
        await message.reply_text("Queue:\n" + "\n".join(lines), parse_mode=None)

    @bot.on_message(filters.command(["pause"], prefixes=["/", "!"]))
    async def pause_handler(_: Client, message: Message) -> None:
        if not await _is_admin(message):
            await message.reply_text("Only group admins can pause playback.")
            return
        await message.reply_text("Paused the stream." if await player.pause(message.chat.id) else "Nothing is playing to pause.")

    @bot.on_message(filters.command(["resume"], prefixes=["/", "!"]))
    async def resume_handler(_: Client, message: Message) -> None:
        if not await _is_admin(message):
            await message.reply_text("Only group admins can resume playback.")
            return
        await message.reply_text("Resumed the stream." if await player.resume(message.chat.id) else "Nothing is paused to resume.")

    @bot.on_message(filters.command(["skip", "next"], prefixes=["/", "!"]))
    async def skip_handler(_: Client, message: Message) -> None:
        if not await _is_admin(message):
            await message.reply_text("Only group admins can skip tracks.")
            return
        bumped = await player.skip(message.chat.id)
        await message.reply_text("Skipped the current track." if bumped else "Nothing to skip -- the queue is empty.")

    @bot.on_message(filters.command(["stop"], prefixes=["/", "!"]))
    async def stop_handler(_: Client, message: Message) -> None:
        if not await _is_admin(message):
            await message.reply_text("Only group admins can stop playback.")
            return
        await player.stop(message.chat.id)
        await message.reply_text("Left the voice chat and cleared the queue.")

    @bot.on_message(filters.command(["play"], prefixes=["/", "!"]))
    async def play_handler(_: Client, message: Message) -> None:
        query = " ".join(message.command[1:])
        if not query:
            await message.reply_text("Give me a song name or link, e.g. /play thriller")
            return
        status_msg = await message.reply_text("Searching for " + _safe(query), parse_mode=None)
        try:
            requester = message.from_user.first_name if message.from_user and message.from_user.first_name else "Anonymous"
            item = await player.add(query, requester)
            await player.play(message.chat.id, item)
            await status_msg.edit_text("Playing: " + _safe(item.title), parse_mode=None)
        except Exception as exc:
            LOGGER.exception("Error handling /play")
            await status_msg.edit_text(_safe(_translate(exc)), parse_mode=None)
