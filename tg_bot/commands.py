"""Full command surface for the music bot (modular, no VC powers here).

The bot (`bot`, BOT_TOKEN) hears these in any group it is a member of.  It never
touches the voice chat directly -- every surface forwards to the spare-account
worker (`player`), which is the only thing that owns PyTgCalls.  Admin-gated
commands (/pause /resume /skip /stop /skip) are enforced from ADMIN_IDS so a
rouge member can't kill someone else's stream.  Every user-facing error is
translated into a short, actionable line -- no raw tracebacks to strangers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from tg_bot.config import ADMIN_IDS, LOGGER, BOT_NAME

if TYPE_CHECKING:
    from tg_bot.player import MusicPlayer, QueueItem

_VC_HINT = (
    "Make sure (1) a voice chat is ACTIVE in this group (start one as an admin), "
    "(2) the assistant account is in the group, and (3) the song resolved to a "
    "non-YouTube stream."
)


def _translate(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "no_active_group_call" in text or "active group call" in text:
        return "No voice chat is running in this group. Start one first, then retry."
    if "user_not_participant" in text or "not a participant" in text or "user not in" in text:
        return "The assistant account isn't in this group yet. Add it, then retry."
    if "already" in text and ("group call" in text or "active" in text):
        return "A stream is already active here -- use /skip or /stop first."
    if "permission" in text or "forbidden" in text or "not allowed" in text:
        return "The assistant is missing permissions (manage voice chat / speak). " + _VC_HINT
    return text or "Something went wrong on the server side."


def _is_admin(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return True  # anonymous admins still get the control surface
    return user.id in ADMIN_IDS


def register(bot: Client, player: "MusicPlayer") -> None:
    """Attach every command handler to the bot client."""

    @bot.on_message(filters.command(["start"], prefixes=["/", "!"]))
    async def start_handler(_: Client, message: Message) -> None:
        await message.reply_text(
            "Hello! I'm the **Music Phoenix** sidecar worker.\n\n"
            "I turn `/play <song | link>` in a group into a live voice-chat stream, "
            "using the assistant account to join the group's voice chat.\n\n"
            "Commands:\n"
            "- /play <query> -- enqueue a song\n"
            "- /queue -- show what's in line\n"
            "- /pause /resume -- pause or resume playback (admins)\n"
            "- /skip -- jump to the next track (admins)\n"
            "- /stop -- leave the voice chat and clear the queue (admins)"
        )

    @bot.on_message(filters.command(["help"], prefixes=["/", "!"]))
    async def help_handler(_: Client, message: Message) -> None:
        await message.reply_text(
            "**Music Phoenix commands**\n\n"
            "- `/play <song name or link>` -- resolve + stream (non-YouTube)\n"
            "- `/queue` -- list the pending tracks\n"
            "- `/pause` / `/resume` -- control playback (admins)\n"
            "- `/skip` -- go to the next track (admins)\n"
            "- `/stop` -- leave the VC and clear the queue (admins)"
        )

    @bot.on_message(filters.command(["queue"], prefixes=["/", "!"]))
    async def queue_handler(_: Client, message: Message) -> None:
        items = player.queue_list(message.chat.id)
        current = player.now_playing(message.chat.id)
        if not items and current is None:
            await message.reply_text("The queue is empty. Use /play to add a track.")
            return
        lines = []
        if current is not None:
            lines.append(f"▶ **Now playing:** {current.title}")
        for i, item in enumerate(items, 1):
            lines.append(f"{i}. {item.title}")
        await message.reply_text("**Queue:**\n" + "\n".join(lines))

    @bot.on_message(filters.command(["pause"], prefixes=["/", "!"]))
    async def pause_handler(_: Client, message: Message) -> None:
        if not _is_admin(message):
            await message.reply_text("Only group admins can pause playback.")
            return
        ok = await player.pause(message.chat.id)
        await message.reply_text("⏸ Paused the stream." if ok else "Nothing is playing to pause.")

    @bot.on_message(filters.command(["resume"], prefixes=["/", "!"]))
    async def resume_handler(_: Client, message: Message) -> None:
        if not _is_admin(message):
            await message.reply_text("Only group admins can resume playback.")
            return
        ok = await player.resume(message.chat.id)
        await message.reply_text("▶ Resumed the stream." if ok else "Nothing is paused to resume.")

    @bot.on_message(filters.command(["skip"], prefixes=["/", "!"]))
    async def skip_handler(_: Client, message: Message) -> None:
        if not _is_admin(message):
            await message.reply_text("Only group admins can skip tracks.")
            return
        bumped = await player.skip(message.chat.id)
        if bumped is not None:
            await message.reply_text(f"⏭ Skipped **{bumped.title}**.")
        else:
            await message.reply_text("Nothing to skip -- the queue is empty.")

    @bot.on_message(filters.command(["stop"], prefixes=["/", "!"]))
    async def stop_handler(_: Client, message: Message) -> None:
        if not _is_admin(message):
            await message.reply_text("Only group admins can stop playback.")
            return
        was = await player.stop(message.chat.id)
        await message.reply_text(
            "🛑 Left the voice chat and cleared the queue." if was else
            "Nothing was playing, but I've left the call anyway."
        )

    @bot.on_message(filters.command(["play"], prefixes=["/", "!"]))
    async def play_handler(client: Client, message: Message) -> None:
        chat_id = message.chat.id
        query = " ".join(message.command[1:])
        if not query:
            await message.reply_text("Give me a song name or link to play, e.g. /play thriller")
            return

        status_msg = await message.reply_text(f"🔍 Searching for {query}...")
        try:
            requester = (
                f"{message.from_user.first_name}"
                if message.from_user and message.from_user.first_name
                else "Anonymous"
            )
            item = await player.add(query, requester)
            await player.play(chat_id, item)
            await status_msg.edit_text(f"▶️ Playing: {item.title}")
        except Exception as exc:
            LOGGER.exception("Error handling /play")
            await status_msg.edit_text(f"❌ {_translate(exc)}")
