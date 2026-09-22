"""Inline playback callback handlers with requester/admin authorization."""

from pyrogram import Client, filters
from pyrogram.errors import QueryIdInvalid

from tg_bot.config import LOGGER
from tg_bot.modules.playback import _can_control, controls


def register(bot: Client, player) -> None:
    @bot.on_callback_query(filters.regex(r"^music:(-?\d+):(pause|resume|skip|stop)$"))
    async def music_callback(_, query):
        try:
            _, raw_chat, action = query.data.split(":", 2)
        except Exception:
            return
        chat_id = int(raw_chat)
        uid = query.from_user.id if query.from_user else None
        chat = query.message.chat if query.message is not None else None
        allowed = await _can_control(player, chat, uid)
        try:
            if not allowed:
                await query.answer(
                    "Only the requester or a group admin can control playback.",
                    show_alert=True,
                )
                return
            await query.answer()
        except QueryIdInvalid:
            LOGGER.debug("callback query id expired; continuing anyway")

        try:
            if action == "pause":
                ok = await player.pause(chat_id)
            elif action == "resume":
                ok = await player.resume(chat_id)
            elif action == "skip":
                ok = bool(await player.skip(chat_id))
            else:
                ok = await player.stop(chat_id)
            if query.message and action in ("pause", "resume"):
                await query.message.edit_reply_markup(
                    controls(chat_id, paused=action == "pause")
                )
            elif query.message and not ok:
                await query.message.edit_text(
                    "Nothing to do.", parse_mode=None
                )
        except Exception:
            LOGGER.exception("Playback control failed")
            if query.message:
                await query.message.edit_text(
                    "Playback control failed.", parse_mode=None
                )