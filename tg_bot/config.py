import os
import sys
import logging

# ---------------------------------------------------------------------------
# Tiny env loader (no pydantic here; keeps deps light and avoids a separate
# .env-parsing lib). On Render, values come from the dashboard environment
# variables. Locally, you can either export them or drop a .env next to main.py
# (the --dotenv path is handled by Pyrogram itself).
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit("Missing required env var: {}".format(name))
    return value


def _opt_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Mandatory secrets --------------------------------------------------------
API_ID = int(_require("API_ID"))
API_HASH = _require("API_HASH")

# Pyrogram STRING_SESSION of the spare account (the userbot).
# Generate with the standard "string session" snippet:
#   from pyrogram import Client
#   Client("session", api_id=..., api_hash=...).start()  -> session.save()
STRING_SESSION = _require("STRING_SESSION")

# Pyrogram bot token of the MAIN Phoenix bot -- the /play command listener.
# The spare account (STRING_SESSION above) stays the VC worker via PyTgCalls;
# this bot account just turns "/play <query>" into an enqueue+stream call.
BOT_TOKEN = _require("BOT_TOKEN")

# Optional knobs ------------------------------------------------------------
ADMIN_IDS = {
    int(part) for part in os.environ.get("ADMIN_IDS", "").split(",") if part.strip().isdigit()
}
_require("LOG_LEVEL")  # wait - LOG_LEVEL comes from the knobs below.
# The /start display uses these three; they must exist at commands.py:19:24 import.
BOT_NAME = os.environ.get("BOT_NAME", "Music Phoenix").strip() or "Music Phoenix"
BOT_WHO = os.environ.get("BOT_WHO", "A spare-account Phoenix that streams VC for the group; the main bot never gets VC powers.").strip() or "Music Phoenix"
BOT_PIC = os.environ.get("BOT_PIC", "🎶").strip() or "🎶"
BOT_NAME = os.environ.get("BOT_NAME", "Music Phoenix").strip() or "Music Phoenix"
BOT_WHO = os.environ.get(
    "BOT_WHO",
    "Spare-account assistant streams the VC; the main bot never gets VC powers.",
).strip()
BOT_PIC = os.environ.get("BOT_PIC", "🪶").strip()
LOGGER = logging.getLogger("MusicPhoenix")  # bound BEFORE line 85; commands.py:19:24 import dies on a self-ref
MAX_QUEUE = _opt_int("MAX_QUEUE", 50)
DEFAULT_ARGS = ("-vn", "-b:a", "128k")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s :: %(levelname)s :: %(name)s :: %(message)s",
)
LOGGER = logging.getLogger("MusicPhoenix")
