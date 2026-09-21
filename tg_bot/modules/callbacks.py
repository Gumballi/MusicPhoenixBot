"""Inline playback callback handlers with the same admin policy as commands."""
from pyrogram import Client, filters
from tg_bot.config import ADMIN_IDS
from tg_bot.modules.playback import _is_admin, controls

def register(bot: Client, player) -> None:
    @bot.on_callback_query(filters.regex(r"^music:(-?\d+):(pause|resume|skip|stop)$"))
    async def music_callback(_, query):
        try:
            _, raw_chat, action = query.data.split(":", 2)
            chat_id = int(raw_chat)
            if query.from_user and query.from_user.id in ADMIN_IDS:
                allowed = True
            else:
                member = await query.message.chat.get_member(query.from_user.id) if query.message else None
                allowed = bool(member and member.status.value in ("administrator", "creator"))
            if not allowed:
                await query.answer("Only group admins can control playback.", show_alert=True); return
            if action == "pause": ok = await player.pause(chat_id); text = "Paused the stream."
            elif action == "resume": ok = await player.resume(chat_id); text = "Resumed the stream."
            elif action == "skip": ok = bool(await player.skip(chat_id)); text = "Skipped the current track."
            else: ok = await player.stop(chat_id); text = "Stopped playback."
            await query.answer(text if ok else "Nothing to do.")
            current = player.now_playing(chat_id)
            if query.message and action in ("pause", "resume"):
                await query.message.edit_reply_markup(controls(chat_id, paused=(action == "pause")))
            elif query.message and current:
                await query.message.edit_text("▶ Now playing: " + current.title, reply_markup=controls(chat_id), parse_mode=None)
        except Exception:
            await query.answer("Playback control failed.", show_alert=True)
