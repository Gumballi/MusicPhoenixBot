"""Resolver: query -> one fully-downloaded local audio file."""

import glob
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

LOGGER = logging.getLogger(__name__)

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

GATED_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "ggpht.com")

# Anything shorter than this from a search is almost certainly a gated preview,
# not the track. Streaming it looks exactly like "the bot cut out after 20s".
MIN_DURATION = 45


class ResolveError(Exception):
    pass


def _is_gated(url: str) -> bool:
    hostname = (urlparse(url or "").hostname or "").lower()
    return any(
        hostname == host or hostname.endswith("." + host) for host in GATED_HOSTS
    )


def _downloaded_path(info: dict) -> Optional[str]:
    """FIX: the old code built '/tmp/mp_<id>.<extractor_name>' -- it used the
    EXTRACTOR as the file extension, so it never matched and always fell
    through. Ask yt-dlp where it actually put the file."""
    for entry in info.get("requested_downloads") or []:
        path = entry.get("filepath") or entry.get("_filename")
        if path and os.path.exists(path):
            return path
    path = info.get("filepath") or info.get("_filename")
    if path and os.path.exists(path):
        return path
    # FIX: on datacenter IPs (Render), yt-dlp materializes the file straight
    # into the outtmpl and the info dict never carries filepath/_filename.
    # The track id is the one byte we always hold -- glob the output template.
    track_id = info.get("id")
    if track_id:
        for matched in glob.glob("/tmp/mp_{}.*".format(track_id)):
            if not matched.endswith(".part"):
                return matched
    return None


def resolve_track(query: str) -> dict:
    query = (query or "").strip()
    if not query:
        raise ResolveError("Empty music query")

    is_url = query.startswith(("http://", "https://"))
    if is_url and _is_gated(query):
        raise ResolveError(
            "YouTube links are blocked from this host. Try a SoundCloud or "
            "Bandcamp link, or search by name."
        )
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

    title = info.get("title") or query
    duration = info.get("duration") or 0

    local_path = _downloaded_path(info)
    if not local_path:
        raise ResolveError("Download did not materialize for {!r}".format(title))

    # FIX: reject previews. ffmpeg plays a 20s preview to EOF and the call goes
    # silent -- indistinguishable from a crash.
    if duration and duration < MIN_DURATION:
        try:
            os.remove(local_path)
        except OSError:
            pass
        raise ResolveError(
            "Only a {:.0f}s preview is available for {!r} -- the full track is "
            "gated from this IP. Try a direct link instead.".format(duration, title)
        )

    return {"title": title, "url": local_path, "webpage": info.get("webpage_url")}
