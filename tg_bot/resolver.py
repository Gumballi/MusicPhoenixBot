"""Tiny resolver: query -> ONE direct audio URL.

Render's datacenter IP gates YouTube, so we refuse to
hand a gated playable URL to the player.  yt-dlp extract_info(download=False)
still parses single queries from a datacenter for hosts that don't gate
(SoundCloud, Bandcamp, direct http).  Nothing here touches Telegram --
"string in, URL out".
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

LOGGER = logging.getLogger(__name__)

# bestaudio/best is enough for every non-YouTube source we target.
# skip_download must be False for SoundCloud: direct CDN URLs handed to the
# player over Render's IP are throttled to silence at ~10-20s.  Download into
# /tmp once, stream the local file, zero remote-socket stalls.
YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "noprogress": True,
    "no_warnings": True,
    "skip_download": False,
    "outtmpl": "/tmp/mp_%(id)s.%(ext)s",
    "nocheckcertificate": True,
    "socket_timeout": 15,
    "retries": 3,
}

# Hosts whose direct stream we refuse to hand the player from a datacenter.
GATED_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "ggpht.com")


class ResolveError(Exception):
    pass


def _is_gated(url: str) -> bool:
    hostname = (urlparse(url or "").hostname or "").lower()
    return any(
        hostname == host or hostname.endswith("." + host)
        for host in GATED_HOSTS
    )


def _is_youtube_service(service: str) -> bool:
    normalized = (service or "").lower().replace(" ", "")
    return normalized.startswith("youtube")


def resolve_track(query: str) -> dict:
    """Return {"title", "url", "webpage"} or raise ResolveError.

    url is ALWAYS a local /tmp filepath now.  Render's datacenter IP gets
    SoundCloud's CDN socket throttled to roughly nothing ~10-20s in, so
    handing a remote CDN URL to FFmpeg guarantees the exact EOF-death Poke
    saw.  yt-dlp downloads into /tmp once; the player streams a file it owns.
    """
    query = (query or "").strip()
    if not query:
        raise ResolveError("Empty music query")

    is_url = query.startswith(("http://", "https://"))
    source_query = query if is_url else "scsearch1:" + query

    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        try:
            info = ydl.extract_info(source_query, download=True)
        except Exception as exc:
            raise ResolveError("yt-dlp failed for {!r}: {}".format(query, exc)) from exc

    if not info:
        raise ResolveError("Nothing resolved for {!r}".format(query))

    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    service = (info.get("extractor_key") or "")
    id_ = info.get("id") or "track"
    local_path = "/tmp/mp_{}.{}".format(id_, info.get("extractor", "webm"))

    if not os.path.exists(local_path):
        downloads = info.get("requested_downloads") or []
        if downloads:
            local_path = (downloads[0].get("filepath") or local_path)
        if not os.path.exists(local_path):
            raise ResolveError("Download did not materialize at {!r}".format(local_path))

    webpage = info.get("webpage_url")

    return {"title": info.get("title") or query, "url": local_path, "webpage": webpage}
