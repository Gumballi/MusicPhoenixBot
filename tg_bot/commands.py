"""Compatibility entry point for modular command registration."""
from tg_bot.modules.callbacks import register as register_callbacks
from tg_bot.modules.playback import register as register_playback

def register(bot, player) -> None:
    register_playback(bot, player)
    register_callbacks(bot, player)
