"""Resolve music queries through JioSaavn, SoundCloud, then yt-dlp."""

import glob
import json
import logging
import os
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yt_dlp

LOGGER = logging.getLogger(__name__)
MIN_DURATION = 45
SAAVN_ENDPOINT = "https://www.jiosaavn.com/api.php"

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
    "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
}


class ResolveError(Exception):
    pass


def _downloaded_path(info: dict) -> Optional[str]:
    for entry in info.get("requested_downloads") or []:
        path = entry.get("filepath") or entry.get("_filename")
        if path and os.path.exists(path):
            return path
    path = info.get("filepath") or info.get("_filename")
    if path and os.path.exists(path):
        return path
    track_id = info.get("id")
    if track_id:
        for matched in glob.glob("/tmp/mp_{}.*".format(track_id)):
            if not matched.endswith(".part"):
                return matched
    return None


def _value(song: dict, *keys: str):
    for key in keys:
        value = song.get(key)
        if value not in (None, ""):
            return value
    return None


def _jiosaavn_search(query: str) -> Optional[dict]:
    """Use the synchronous public endpoint because resolve_track runs in to_thread."""
    params = {
        "__call": "search.getResults",
        "_format": "json",
        "n": 5,
        "p": 1,
        "q": query,
        "_marker": 0,
        "api_version": 4,
        "ctx": "web6dot0",
    }
    url = SAAVN_ENDPOINT + "?" + urlencode(params)
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        LOGGER.info("JioSaavn lookup failed: %s", exc)
        return None

    songs = payload.get("results") or payload.get("data") or []
    if isinstance(songs, dict):
        songs = songs.get("results") or songs.get("songs") or []
    if not songs:
        return None
    song = songs[0]
    stream = _value(song, "download_url", "downloadUrl", "media_url", "mediaUrl")
    if isinstance(stream, list):
        stream = (stream[-1] or {}).get("url") if stream else None
    if isinstance(stream, dict):
        stream = stream.get("url")
    if not stream:
        # JioSaavn sometimes exposes only the encrypted field; never return it
        # as a playable URL, and let the next provider handle the query.
        return None
    return {
        "title": _value(song, "song", "title", "name") or query,
        "url": stream,
        "webpage": _value(song, "url", "perma_url", "permaUrl"),
    }


def _extract(source: str, label: str, reject_short: bool = False) -> Optional[dict]:
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(source, download=True)
    except Exception as exc:
        LOGGER.info("%s resolution failed: %s", label, exc)
        return None
    if not info:
        return None
    if info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        return None
    path = _downloaded_path(info)
    duration = info.get("duration") or 0
    if reject_short and duration and duration < MIN_DURATION:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        LOGGER.info("Ignoring %ss %s preview", duration, label)
        return None
    if not path:
        return None
    return {"title": info.get("title") or label, "url": path, "webpage": info.get("webpage_url")}


def resolve_track(query: str) -> dict:
    query = (query or "").strip()
    if not query:
        raise ResolveError("Empty music query")

    if query.startswith(("http://", "https://")):
        result = _extract(query, "direct URL")
    else:
        result = _jiosaavn_search(query)
        if result:
            return result
        # A short SoundCloud preview is a failed provider, not a terminal error.
        result = _extract("scsearch1:" + query, "SoundCloud", reject_short=True)
        if result is None:
            result = _extract("ytsearch1:" + query, "YouTube", reject_short=True)

    if result is None:
        raise ResolveError("No full-length playable result found for {!r}".format(query))
    return result
