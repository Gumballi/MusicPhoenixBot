"""
Phoenix Music sidecar - dual-client architecture.
"""
import asyncio
import logging
from importlib import import_module
from pyrogram import idle, Client
from tg_bot.config import API_ID, API_HASH, STRING_SESSION, BOT_TOKEN, LOGGER
from tg_bot.health import start_health_server
from tg_bot.player import MusicPlayer

logging.basicConfig(level=LOGGER.level, format="%(asctime)s :: %(name)s :: %(levelname)s :: %(message)s")

async def _boot() -> None:
    start_health_server()
    bot = Client("music_phoenix_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    assistant = Client("music_phoenix_assistant", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION, in_memory=True, no_updates=False)
    player = MusicPlayer(assistant)
    await bot.start(); await assistant.start(); await player.start()
    for name in ("playback", "callbacks", "search"):
        import_module(f"tg_bot.modules.{name}").register(bot, player)
    LOGGER.info("Music Phoenix is online and ready for /play in groups!")
    await idle()
    await bot.stop(); await assistant.stop()

if __name__ == "__main__":
    asyncio.run(_boot())
