"""Inline playback callback handlers with requester/admin authorization."""
from pyrogram import Client, filters
from tg_bot.config import ADMIN_IDS
from tg_bot.modules.playback import controls

def register(bot:Client,player)->None:
    @bot.on_callback_query(filters.regex(r"^music:(-?\d+):(pause|resume|skip|stop)$"))
    async def music_callback(_,query):
        try:
            _,raw_chat,action=query.data.split(":",2);chat_id=int(raw_chat);current=player.now_playing(chat_id);uid=query.from_user.id if query.from_user else None
            allowed=uid in ADMIN_IDS or bool(current and current.requester==uid)
            if not allowed and query.message:
                try:
                    member=await query.message.chat.get_member(uid);allowed=member.status.value in ("administrator","creator")
                except Exception:allowed=False
            if not allowed:return await query.answer("Only the requester or a group admin can control playback.",show_alert=True)
            if action=="pause":ok=await player.pause(chat_id);text="Paused the stream."
            elif action=="resume":ok=await player.resume(chat_id);text="Resumed the stream."
            elif action=="skip":ok=bool(await player.skip(chat_id));text="Skipped the current track."
            else:ok=await player.stop(chat_id);text="Stopped playback."
            await query.answer(text if ok else "Nothing to do.")
            if query.message and action in ("pause","resume"):await query.message.edit_reply_markup(controls(chat_id,paused=action=="pause"))
        except Exception:await query.answer("Playback control failed.",show_alert=True)
