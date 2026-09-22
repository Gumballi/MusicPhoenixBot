"""Inline playback callback handlers with requester/admin authorization."""

from pyrogram import Client, filters
from pyrogram.errors import QueryIdInvalid

from tg_bot.config import ADMIN_IDS, LOGGER
from tg_bot.modules.playback import controls


def register(bot: Client, player) -> None:
    @bot.on_callback_query(filters.regex(r"^music:(-?\d+):(pause|resume|skip|stop)$"))
    async def music_callback(_, query):
        try:
            _, raw_chat, action = query.data.split(":", 2)
        except Exception:
            return
        chat_id = int(raw_chat)
        uid = query.from_user.id if query.from_user else None
        current = player.now_playing(chat_id)
        allowed = uid in ADMIN_IDS or bool(current and current.requester == uid)
        if not allowed and query.message is not None and uid is not None:
            try:
                member = await query.message.chat.get_member(uid)
                allowed = member.status.value in ("administrator", "creator")
            except Exception:
                allowed = False
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