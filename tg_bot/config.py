import logging
import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _opt_int(name: str, default: int) -> int:
    val = os.environ.get(name, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


API_ID = int(_require("API_ID"))
API_HASH = _require("API_HASH")
BOT_TOKEN = _require("BOT_TOKEN")
STRING_SESSION = _require("STRING_SESSION")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s :: %(levelname)s :: %(name)s :: %(message)s",
)
logging.getLogger("pytgcalls").setLevel(logging.DEBUG)

LOGGER = logging.getLogger("MusicPhoenix")

BOT_NAME = os.environ.get("BOT_NAME", "Music Phoenix").strip() or "Music Phoenix"
BOT_WHO = os.environ.get(
    "BOT_WHO",
    "Spare-account assistant streams the VC; the main bot never gets VC powers.",
).strip()
BOT_PIC = os.environ.get("BOT_PIC", "🪶").strip()

MAX_QUEUE = _opt_int("MAX_QUEUE", 50)

# main.py:12 imports ADMIN_IDS at boot; without a binding Render dies with
# `ImportError: cannot import name 'ADMIN_IDS'` before a single log line.
ADMIN_IDS: tuple = tuple(
    int(part.strip())
    for part in os.environ.get("ADMIN_IDS", "").split(",")
    if part.strip().isdigit()
)
