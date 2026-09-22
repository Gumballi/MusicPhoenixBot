"""Search-result picker for /play text queries (metadata-only, Render-safe).

Holds short-lived in-memory sessions keyed by a small token so inline
callback data stays inside Telegram's 64-byte limit.
"""

from __future__ import annotations

import asyncio
import html
import secrets
import time
from typing import Optional

from pyrogram import Client, enums, filters
from pyrogram.errors import QueryIdInvalid
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_bot.config import ADMIN_IDS, LOGGER
from tg_bot.resolver import ResolveError, resolve_selected

PAGE_SIZE = 5
TTL = 300
MAX_SESSIONS = 200


class SearchSession:
    __slots__ = ("chat_id", "user_id", "query", "results", "page", "expires", "picking")

    def __init__(self, chat_id: int, user_id: Optional[int], query: str, results: list):
        self.chat_id = chat_id
        self.user_id = user_id
        self.query = query
        self.results = results
        self.page = 0
        self.expires = time.time() + TTL
        self.picking = False


SESSIONS: dict[str, SearchSession] = {}


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _dur(seconds) -> str:
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return "?:??"
    if s <= 0:
        return "?:??"
    m, ss = divmod(s, 60)
    return f"{m}:{ss:02d}"


def _render(sess: SearchSession, token: str) -> tuple[str, InlineKeyboardMarkup]:
    results = sess.results
    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    start = sess.page * PAGE_SIZE
    page_items = results[start : start + PAGE_SIZE]
    lines = [
        f"🔍 Results for <i>{_safe(sess.query)}</i> (Page {sess.page + 1}/{total_pages}):"
    ]
    for i, item in enumerate(page_items, start + 1):
        artist = f" — {_safe(item.get('artist'))}" if item.get("artist") else ""
        badge = {"jio": "🟢", "sc": "🔵", "yt": "🔴"}.get(str(item.get("kind")), "⚪")
        lines.append(
            f"{badge} <b>{i}.</b> {_safe(item.get('title'))}{artist} ({_dur(item.get('duration'))})"
        )
    row = [
        InlineKeyboardButton(str(n), callback_data=f"s:{token}:pick:{i}")
        for i, n in enumerate(range(start + 1, start + 1 + len(page_items)))
    ]
    kb = [row] if row else []
    nav = []
    if sess.page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"s:{token}:prev"))
    nav.append(InlineKeyboardButton("🗑 Cancel", callback_data=f"s:{token}:cancel"))
    if sess.page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"s:{token}:next"))
    kb.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(kb)


def open_search(chat_id: int, user_id: Optional[int], query: str, results: list) -> tuple[str, str, InlineKeyboardMarkup]:
    while len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(next(iter(SESSIONS)))
    token = secrets.token_hex(4)
    while token in SESSIONS:
        token = secrets.token_hex(4)
    sess = SearchSession(chat_id=chat_id, user_id=user_id, query=query, results=results)
    SESSIONS[token] = sess
    text, markup = _render(sess, token)
    return token, text, markup


def register(bot: Client, player) -> None:
    from tg_bot.modules.playback import auto_delete, controls, is_group_admin

    @bot.on_callback_query(filters.regex(r"^s:([0-9a-z]+):(pick:\d+|prev|next|cancel)$"))
    async def search_callback(_, query):
        import re

        m = re.match(r"^s:([0-9a-z]+):(pick:\d+|prev|next|cancel)$", query.data or "")
        if not m:
            return
        token, action = m.group(1), m.group(2)
        sess = SESSIONS.get(token)
        uid = query.from_user.id if query.from_user else None
        chat = query.message.chat if query.message else None

        if sess is None or time.time() > sess.expires:
            SESSIONS.pop(token, None)
            try:
                await query.answer("This search has expired.", show_alert=True)
            except QueryIdInvalid:
                pass
            return

        if not (uid == sess.user_id or uid in ADMIN_IDS or await is_group_admin(chat, uid)):
            try:
                await query.answer(
                    "This search belongs to someone else.", show_alert=True
                )
            except QueryIdInvalid:
                pass
            return

        try:
            await query.answer()
        except QueryIdInvalid:
            pass

        if action == "cancel":
            SESSIONS.pop(token, None)
            try:
                await query.message.edit_text("Search cancelled.", parse_mode=None)
            except Exception:
                pass
            asyncio.create_task(auto_delete(query.message, delay=7))
            return

        if action == "prev":
            if sess.page > 0:
                sess.page -= 1
            text, markup = _render(sess, token)
            await query.message.edit_text(
                text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
            )
            return

        if action == "next":
            total_pages = max(1, (len(sess.results) + PAGE_SIZE - 1) // PAGE_SIZE)
            if sess.page + 1 < total_pages:
                sess.page += 1
            text, markup = _render(sess, token)
            await query.message.edit_text(
                text, reply_markup=markup, parse_mode=enums.ParseMode.HTML
            )
            return

        if not action.startswith("pick:"):
            return
        try:
            rel = int(action[5:])
        except ValueError:
            return
        idx = sess.page * PAGE_SIZE + rel
        if not (0 <= idx < len(sess.results)):
            return
        if sess.picking:
            try:
                await query.answer(
                    "A selection is already downloading — hang tight.",
                    show_alert=True,
                )
            except QueryIdInvalid:
                pass
            return
        cand = sess.results[idx]

        title = cand.get("title") or cand.get("src", "")
        try:
            await query.message.edit_text(f"Fetching {_safe(title)}…", parse_mode=None)
        except Exception:
            pass

        sess.picking = True
        try:
            info = await asyncio.to_thread(resolve_selected, cand)
            item = await player.add_resolved(info, uid)
            await player.play(sess.chat_id, item)
        except ResolveError as exc:
            sess.picking = False
            LOGGER.warning("search pick failed: %s", exc)
            hint = ""
            if cand.get("kind") == "yt":
                hint = (
                    "\n\n(badges: 🟢 JioSaavn · 🔵 SoundCloud · 🔴 YouTube — "
                    "YouTube often refuses datacenter IPs, so try a green or blue one)"
                )
            try:
                text, markup = _render(sess, token)
                await query.message.edit_text(
                    "❌ That pick failed: " + _safe(str(exc)) + hint + "\n\n" + text,
                    reply_markup=markup,
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            return
        except Exception:
            sess.picking = False
            LOGGER.exception("search pick crashed")
            try:
                await query.message.edit_text(
                    "Failed to start that track.", parse_mode=None
                )
            except Exception:
                pass
            asyncio.create_task(auto_delete(query.message, delay=7))
            return

        SESSIONS.pop(token, None)
        try:
            await query.message.edit_text(
                "▶ Now playing: " + _safe(info.get("title") or title),
                reply_markup=controls(sess.chat_id),
                parse_mode=None,
            )
        except Exception:
            pass
        await player.swap_card(sess.chat_id, item, query.message)