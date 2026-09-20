"""Tiny resolver: query -> ONE direct audio URL.

Render's datacenter IP gates YouTube, so we refuse to
hand a gated playable URL to the player.  yt-dlp extract_info(download=False)
still parses single queries from a datacenter for hosts that don't gate
(SoundCloud, Bandcamp, direct http).  Nothing here touches Telegram --
"string in, URL out".
"""

import logging
from typing import Optional

import yt_dlp

LOGGER = logging.getLogger(__name__)

# bestaudio/best is enough for every non-YouTube source we target.
YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "noprogress": True,
    "no_warnings": True,
    "skip_download": True,
    "nocheckcertificate": True,
    "socket_timeout": 15,
    "retries": 3,
}

# Hosts whose direct stream we refuse to hand the player from a datacenter.
GATED_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "ggpht.com")


class ResolveError(Exception):
    pass


def _is_gated(url: str) -> bool:
    return any(h in (url or "").lower() for h in GATED_HOSTS)


def resolve_track(query: str) -> dict:
    """Return {"title", "url", "webpage"} or raise ResolveError."""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        try:
            info = ydl.extract_info(query, download=False)
        except Exception as exc:
            raise ResolveError("yt-dlp failed for {!r}: {}".format(query, exc)) from exc

    if not info:
        raise ResolveError("Nothing resolved for {!r}".format(query))

    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    url = (info.get("url") or "").strip()
    service = (info.get("extractor_key") or "")
    webpage = info.get("webpage_url")

    if not url:
        raise ResolveError("No playable stream for {!r} (service {})".format(query, service))

    if _is_gated(url):
        raise ResolveError(
            "Refusing gated source {} for {!r} -- Render IPs get 403s there. "
            "Try a SoundCloud/Bandcamp/direct-http link.".format(service, query)
        )

    return {"title": info.get("title") or query, "url": url, "webpage": webpage}
