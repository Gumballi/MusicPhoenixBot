"""Playback commands, kept separate from the audio worker."""

from __future__ import annotations

import asyncio
import html
from typing import Optional

from pyrogram import Client, enums, filters
from pyrogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from tg_bot.config import (
    ADMIN_IDS,
    ASSISTANT_USERNAME,
    BOT_NAME,
    BOT_PIC,
    BOT_WHO,
    LOGGER,
    MAX_QUEUE,
)


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


async def auto_delete(message, delay: int = 10) -> None:
    """Delete a message after a short delay so transient replies don't pile up.

    Best effort: bots without can_delete_messages (or messaging race) simply
    fail silently.
    """
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


async def _ephemeral_reply(message: Message, text: str, delay: int = 7, **kwargs) -> None:
    """Reply with a confirmation that vanishes; never touches the user's message."""
    try:
        reply = await message.reply_text(text, **kwargs)
    except Exception:
        return
    asyncio.create_task(auto_delete(reply, delay))


async def _can_control(player, chat, uid: Optional[int]) -> bool:
    """Requester-or-admin gate, shared by text commands and inline buttons.

    Mirrors the inline-callback authorization so /pause and the ⏸️ button agree:
    a track's requester always controls it; otherwise group admins still do.
    """
    if uid is None:
        return False
    if uid in ADMIN_IDS:
        return True
    current = player.now_playing(chat.id) if chat is not None else None
    if current is not None and current.requester == uid:
        return True
    return await is_group_admin(chat, uid)


async def is_group_admin(chat, uid: Optional[int]) -> bool:
    if uid is None or chat is None:
        return False
    try:
        member = await chat.get_member(uid)
        return member.status.value in ("administrator", "creator")
    except Exception:
        return False


def controls(chat_id: int, paused: bool = False) -> InlineKeyboardMarkup:
    p = "resume" if paused else "pause"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️" if paused else "⏸️",
                    callback_data=f"music:{chat_id}:{p}",
                ),
                InlineKeyboardButton(
                    "⏭️", callback_data=f"music:{chat_id}:skip"
                ),
                InlineKeyboardButton(
                    "⏹️", callback_data=f"music:{chat_id}:stop"
                ),
            ]
        ]
    )


def _bot_display(bot: Client) -> str:
    try:
        return bot.me.first_name or BOT_NAME
    except Exception:
        return BOT_NAME


def _bot_username(bot: Client) -> str:
    try:
        return bot.me.username or ""
    except Exception:
        return ""


HELP_TEXT = """
{PIC} <b>{bot_name}</b> — I stream music straight into any group's voice chat. No downloads, no waiting: you
give me a song or a link, and the sidecar account joins the call and plays it.

<b>Commands</b> (all also work with ! instead of /)
 └ /play &lt;song name or link&gt; — a link streams right away; a text search shows 5 choices to pick from
   (metadata only, nothing downloaded until you choose). I look up JioSaavn, then SoundCloud, then YouTube.
 └ /queue — show what's next in line.
 └ /pause — pause the current track.
 └ /resume — resume the current track.
 └ /skip or /next — jump to the next track (they are the same command).
 └ /stop — leave the voice chat and clear the whole queue.
 └ /settings — show the current toggles (queue limit, requester controls, inline buttons).

<b>Who can control playback?</b>
 • The person who requested the current track, and group admins, can pause, resume, skip or stop.
 • Anyone can /play and /queue.
 • The <b>▶ Now playing</b> banner carries inline ⏸️ ⏭️ ⏹️ buttons, so no one needs to type a command.

{BOT_WHO}
""".format(
    PIC=BOT_PIC,
    bot_name=BOT_NAME,
    min_duration=45,
    BOT_WHO=BOT_WHO,
)


def register(bot: Client, player) -> None:
    @bot.on_message(filters.command(["start"], prefixes=["/", "!"]))
    async def start_handler(_, message: Message):
        name = (
            message.from_user.first_name
            if message.from_user and message.from_user.first_name
            else "friend"
        )
        bot_name = _bot_display(bot)
        username = _bot_username(bot)
        if message.chat.type == enums.ChatType.PRIVATE:
            text = (
                f"{BOT_PIC} Hi {_safe(name)}, my name is {_safe(bot_name)}!\n\n"
                "I stream music straight into any group's voice chat — add me to a group and type /play, "
                "then sit back. You can find the full list of what I can do with /help."
            )
            markup = None
            if username:
                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                f"Add {_safe(bot_name)} to your group",
                                url=f"http://t.me/{username}?startgroup=botstart",
                            )
                        ]
                    ]
                )
            await message.reply_text(text, reply_markup=markup, parse_mode=None)
            return
        await message.reply_text(
            f"{BOT_PIC} I'm {_safe(bot_name)} — use /play &lt;song or link&gt; to start streaming music here.",
            parse_mode=enums.ParseMode.HTML,
        )

    @bot.on_chat_member_updated()
    async def member_update_handler(_, update: ChatMemberUpdated):
        chat_type = update.chat.type
        if chat_type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            return
        new = update.new_chat_member
        if new is None or new.status not in (
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
        ):
            return
        old = update.old_chat_member
        if old is not None and old.status not in (
            enums.ChatMemberStatus.LEFT,
            enums.ChatMemberStatus.BANNED,
        ):
            return
        try:
            if new.user and new.user.id != bot.me.id:
                return
        except Exception:
            return
        bot_name = _bot_display(bot)
        text = (
            f"{BOT_PIC} <b>{_safe(bot_name)}</b> needs two things to stream music here:\n\n"
            "1️⃣ <b>Permission to send messages</b> — I take the least-privilege route: answer my inline "
            "button when it shows up, or just make me an admin.\n"
            f"2️⃣ <b>The spare voice account must join this group.</b> Add @{_safe(ASSISTANT_USERNAME)} here so "
            "it can join the voice channel — I only control the chat, the spare account streams audio.\n\n"
            "Once that's set, an admin types /play &lt;song or link&gt; and we're live."
        )
        try:
            await bot.send_message(update.chat.id, text, parse_mode=enums.ParseMode.HTML)
        except Exception as exc:
            LOGGER.warning("member-update setup message failed: %s", exc)

    @bot.on_message(filters.command(["help"], prefixes=["/", "!"]))
    async def help_handler(_, message: Message):
        text = HELP_TEXT.format(bot_name=_safe(_bot_display(bot)))
        user = message.from_user
        if message.chat.type != enums.ChatType.PRIVATE and user is not None:
            try:
                await bot.send_message(
                    user.id, text, parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                LOGGER.warning("help PM blocked for user %s", user.id)
                return await message.reply_text(
                    f"{BOT_PIC} I tried to PM you /help but you've blocked me. "
                    "Start me in private with /start, then use /help there.",
                    parse_mode=None,
                )
            return await message.reply_text(
                f"{BOT_PIC} I sent the full /help to your private chat.",
                parse_mode=None,
            )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    @bot.on_message(filters.command(["settings"], prefixes=["/", "!"]))
    async def settings_handler(_, message: Message):
        inline = "on (▶ Now playing banner has ⏸️ ⏭️ ⏹️)"
        text = (
            f"{BOT_PIC} <b>Current settings</b> (no database — all live from env):\n\n"
            f"• Queue limit: <b>{MAX_QUEUE}</b> tracks\n"
            f"• Requester controls: <b>enabled</b> — the requester or a group admin can "
            "pause, resume, skip or stop\n"
            f"• Inline control buttons: <b>{inline}</b>\n"
            f"• Resolve order: JioSaavn → SoundCloud → YouTube (min 45s)\n"
            f"• Spare voice account: @{_safe(ASSISTANT_USERNAME)}\n\n"
            "Toggling these is done via env vars on the host, not in chat."
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    @bot.on_message(filters.command(["queue"], prefixes=["/", "!"]))
    async def queue_handler(_, message: Message):
        items = player.queue_list(message.chat.id)
        current = player.now_playing(message.chat.id)
        if not items and current is None:
            return await _ephemeral_reply(
                message,
                "The queue is empty. Use /play &lt;song or link&gt; to add a track.",
                parse_mode=enums.ParseMode.HTML,
            )
        lines = (["▶ Now playing: " + _safe(current.title)] if current else []) + [
            f"{i}. {_safe(x.title)}" for i, x in enumerate(items, 1)
        ]
        if items:
            lines.append(
                f"{len(items)} track(s) queued — /skip or /next advances."
            )
        await _ephemeral_reply(
            message, "Queue:\n" + "\n".join(lines) if lines else "Queue is empty."
        )

    @bot.on_message(filters.command(["pause"], prefixes=["/", "!"]))
    async def pause_handler(_, message: Message):
        uid = message.from_user.id if message.from_user else None
        if not await _can_control(player, message.chat, uid):
            return await _ephemeral_reply(
                message, "Only the requester or a group admin can pause playback."
            )
        await _ephemeral_reply(
            message,
            "Paused the stream."
            if await player.pause(message.chat.id)
            else "Nothing is playing to pause.",
        )

    @bot.on_message(filters.command(["resume"], prefixes=["/", "!"]))
    async def resume_handler(_, message: Message):
        uid = message.from_user.id if message.from_user else None
        if not await _can_control(player, message.chat, uid):
            return await _ephemeral_reply(
                message, "Only the requester or a group admin can resume playback."
            )
        await _ephemeral_reply(
            message,
            "Resumed the stream."
            if await player.resume(message.chat.id)
            else "Nothing is paused to resume.",
        )

    @bot.on_message(filters.command(["skip", "next"], prefixes=["/", "!"]))
    async def skip_handler(_, message: Message):
        uid = message.from_user.id if message.from_user else None
        if not await _can_control(player, message.chat, uid):
            return await _ephemeral_reply(
                message, "Only the requester or a group admin can skip tracks."
            )
        await _ephemeral_reply(
            message,
            "Skipped the current track."
            if await player.skip(message.chat.id)
            else "Nothing to skip -- the queue is empty.",
        )

    @bot.on_message(filters.command(["stop"], prefixes=["/", "!"]))
    async def stop_handler(_, message: Message):
        uid = message.from_user.id if message.from_user else None
        if not await _can_control(player, message.chat, uid):
            return await _ephemeral_reply(
                message, "Only the requester or a group admin can stop playback."
            )
        await player.stop(message.chat.id)
        await player.delete_card(message.chat.id)
        await _ephemeral_reply(message, "Left the voice chat and cleared the queue.")

    @bot.on_message(filters.command(["play"], prefixes=["/", "!"]))
    async def play_handler(_, message: Message):
        query = " ".join(message.command[1:])
        if not query:
            return await _ephemeral_reply(
                message, "Give me a song name or link, e.g. /play thriller"
            )
        status = await message.reply_text(
            "Searching for " + _safe(query), parse_mode=None
        )
        user = message.from_user
        uid = user.id if user else None
        try:
            stripped = query.strip()
            if stripped.startswith(("http://", "https://")):
                item = await player.add(query, uid)
                await player.play(message.chat.id, item)
                await status.edit_text(
                    "▶ Now playing: " + _safe(item.title),
                    reply_markup=controls(message.chat.id),
                    parse_mode=None,
                )
                await player.swap_card(message.chat.id, item, status)
                return
            from tg_bot.resolver import search_tracks
            from tg_bot.modules.search import open_search

            results = await asyncio.to_thread(search_tracks, query)
            if not results:
                raise Exception("No results for that search.")
            _, text, markup = open_search(
                message.chat.id, uid, query, results
            )
            await status.edit_text(
                text,
                reply_markup=markup,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception as exc:
            LOGGER.exception("Error handling /play")
            await status.edit_text(_safe(str(exc)), parse_mode=None)
            asyncio.create_task(auto_delete(status, delay=7))