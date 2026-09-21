"""Playback commands, kept separate from the audio worker."""
from __future__ import annotations
import html
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from tg_bot.config import ADMIN_IDS, LOGGER

def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)

async def _is_admin(message: Message) -> bool:
    user = message.from_user
    if user is None or user.id in ADMIN_IDS:
        return True
    try:
        member = await message.chat.get_member(user.id)
    except Exception:
        return False
    return member.status.value in ("administrator", "creator")

def controls(chat_id: int, paused: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("▶️" if paused else "⏸️", callback_data=f"music:{chat_id}:{'resume' if paused else 'pause'}"), InlineKeyboardButton("⏭️", callback_data=f"music:{chat_id}:skip"), InlineKeyboardButton("⏹️", callback_data=f"music:{chat_id}:stop")]])

def register(bot: Client, player) -> None:
    @bot.on_message(filters.command(["start"], prefixes=["/", "!"]))
    async def start_handler(_, message):
        await message.reply_text("Hello! Use /play <song or link> to stream music.")

    @bot.on_message(filters.command(["help"], prefixes=["/", "!"]))
    async def help_handler(_, message):
        await message.reply_text("/play <song or link> · /queue · /pause · /resume · /skip or /next · /stop")

    @bot.on_message(filters.command(["queue"], prefixes=["/", "!"]))
    async def queue_handler(_, message):
        items = player.queue_list(message.chat.id); current = player.now_playing(message.chat.id)
        if not items and current is None:
            await message.reply_text("The queue is empty. Use /play to add a track."); return
        lines = (["▶ Now playing: " + _safe(current.title)] if current else [])
        lines.extend(f"{i}. {_safe(item.title)}" for i, item in enumerate(items, 1))
        await message.reply_text("Queue:\n" + "\n".join(lines), parse_mode=None)

    @bot.on_message(filters.command(["pause"], prefixes=["/", "!"]))
    async def pause_handler(_, message):
        if not await _is_admin(message): await message.reply_text("Only group admins can pause playback."); return
        await message.reply_text("Paused the stream." if await player.pause(message.chat.id) else "Nothing is playing to pause.")

    @bot.on_message(filters.command(["resume"], prefixes=["/", "!"]))
    async def resume_handler(_, message):
        if not await _is_admin(message): await message.reply_text("Only group admins can resume playback."); return
        await message.reply_text("Resumed the stream." if await player.resume(message.chat.id) else "Nothing is paused to resume.")

    @bot.on_message(filters.command(["skip", "next"], prefixes=["/", "!"]))
    async def skip_handler(_, message):
        if not await _is_admin(message): await message.reply_text("Only group admins can skip tracks."); return
        await message.reply_text("Skipped the current track." if await player.skip(message.chat.id) else "Nothing to skip -- the queue is empty.")

    @bot.on_message(filters.command(["stop"], prefixes=["/", "!"]))
    async def stop_handler(_, message):
        if not await _is_admin(message): await message.reply_text("Only group admins can stop playback."); return
        await player.stop(message.chat.id); await message.reply_text("Left the voice chat and cleared the queue.")

    @bot.on_message(filters.command(["play"], prefixes=["/", "!"]))
    async def play_handler(_, message):
        query = " ".join(message.command[1:])
        if not query: await message.reply_text("Give me a song name or link, e.g. /play thriller"); return
        status = await message.reply_text("Searching for " + _safe(query), parse_mode=None)
        try:
            user = message.from_user
            item = await player.add(query, user.id if user else None, user.first_name if user else "Anonymous")
            await player.play(message.chat.id, item)
            await status.edit_text("▶ Now playing: " + _safe(item.title), reply_markup=controls(message.chat.id), parse_mode=None)
        except Exception as exc:
            LOGGER.exception("Error handling /play")
            await status.edit_text(_safe(str(exc)), parse_mode=None)
