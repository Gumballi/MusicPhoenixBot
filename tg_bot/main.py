"""
Phoenix Music sidecar - dual-client architecture
bot listens for commands, assistant (userbot) streams audio via PyTgCalls
"""

import asyncio
import logging

from pyrogram import idle, Client, filters
from pyrogram.types import Message

from tg_bot.config import (
    API_ID,
    API_HASH,
    STRING_SESSION,
    BOT_TOKEN,
    ADMIN_IDS,
    LOGGER,
)

from tg_bot.health import start_health_server
from tg_bot.player import MusicPlayer

logging.basicConfig(
    level=LOGGER.level,
    format="%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s",
)

# 1. Bot client (listens for commands in group)
bot = Client(
    "music_phoenix_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
)

# 2. Assistant userbot (streams audio into VC)
assistant = Client(
    "music_phoenix_assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION,
    in_memory=True,
    no_updates=True,
)

player = MusicPlayer(assistant)


@bot.on_message(filters.command(["play"], prefixes=["/", "!"]))
async def play_handler(client: Client, message: Message) -> None:
    LOGGER.info(f"Received /play in {message.chat.id} from {message.from_user.id if message.from_user else 'unknown'}")
    chat_id = message.chat.id
    query = " ".join(message.command[1:])
    if not query:
        await message.reply_text("Give me a song name or link to play, e.g. /play thriller")
        return

    status_msg = await message.reply_text(f"🔍 Searching for {query}...")
    try:
        user_id = message.from_user.id if message.from_user else None
        item = await player.add(query, user_id)
        await player.play(chat_id, item)
        await status_msg.edit_text(f"▶️ Playing: {item.title}")
    except Exception as exc:
        LOGGER.exception("Error handling /play")
        await status_msg.edit_text(f"❌ Error: {exc}")


async def _boot() -> None:
    start_health_server()
    LOGGER.info("Starting Bot and Assistant...")
    await bot.start()
    await assistant.start()
    await player.start()
    LOGGER.info("Music Phoenix is online and ready for /play in groups!")
    await idle()
    await bot.stop()
    await assistant.stop()


if __name__ == "__main__":
    asyncio.run(_boot())
