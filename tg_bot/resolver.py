"""Resolve music queries through JioSaavn, SoundCloud, then yt-dlp."""

import base64
import glob
import json
import logging
import os
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yt_dlp
from Crypto.Cipher import DES
from Crypto.Util.Padding import unpad

LOGGER = logging.getLogger(__name__)
MIN_DURATION = 45

YDL_OPTS = {
    "format": "bestaudio/best", "noplaylist": True, "quiet": True,
    "noprogress": True, "no_warnings": True, "skip_download": False,
    "outtmpl": "/tmp/mp_%(id)s.%(ext)s", "nocheckcertificate": True,
    "socket_timeout": 15, "retries": 3,
    "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
}


class ResolveError(Exception):
    pass


def _downloaded_path(info: dict) -> Optional[str]:
    for entry in info.get("requested_downloads") or []:
        path = entry.get("filepath") or entry.get("_filename")
        if path and os.path.exists(path): return path
    path = info.get("filepath") or info.get("_filename")
    if path and os.path.exists(path): return path
    track_id = info.get("id")
    if track_id:
        for matched in glob.glob("/tmp/mp_{}.*".format(track_id)):
            if not matched.endswith(".part"): return matched
    return None


def _value(song: dict, *keys: str):
    for key in keys:
        value = song.get(key)
        if value not in (None, ""): return value
    return None


def _decrypt_media_url(encrypted: str) -> Optional[str]:
    """Decrypt JioSaavn encrypted_media_url locally; no external API is needed."""
    try:
        raw = base64.b64decode(encrypted)
        plain = unpad(DES.new(b"38346591", DES.MODE_ECB).decrypt(raw), DES.block_size)
        url = plain.decode("utf-8").strip()
        if url.startswith(("http://", "https://")): return url
    except Exception as exc:
        LOGGER.warning("JioSaavn media URL decryption failed: %r", exc)
    return None


def _direct_url(song: dict) -> Optional[str]:
    more = song.get("more_info") or {}
    stream = _value(song, "download_url", "downloadUrl", "media_url", "mediaUrl")
    stream = stream or _value(more, "download_url", "downloadUrl", "media_url", "mediaUrl")
    if isinstance(stream, list): stream = (stream[-1] or {}).get("url") if stream else None
    if isinstance(stream, dict): stream = stream.get("url")
    if isinstance(stream, str) and stream.startswith(("http://", "https://")): return stream
    encrypted = _value(song, "encrypted_media_url") or _value(more, "encrypted_media_url")
    return _decrypt_media_url(encrypted) if encrypted else None


def _jiosaavn_search(query: str) -> Optional[dict]:
    requests = [("jiosaavn", "https://www.jiosaavn.com/api.php", {
        "__call": "search.getResults", "_format": "json", "n": 5, "p": 1,
        "q": query, "_marker": 0, "api_version": 4, "ctx": "web6dot0"}),
        ("saavn.dev", "https://saavn.dev/api/search/songs", {"query": query}),
        ("saavn.me", "https://saavn.me/api/search/songs", {"query": query})]
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
               "Accept": "application/json,text/plain,*/*", "Referer": "https://www.jiosaavn.com/"}
    for name, endpoint, params in requests:
        url = endpoint + "?" + urlencode(params)
        try:
            LOGGER.info("JioSaavn request provider=%s url=%s", name, url)
            with urlopen(Request(url, headers=headers), timeout=12) as response:
                status = getattr(response, "status", response.getcode())
                body = response.read().decode("utf-8", "replace")
            LOGGER.info("JioSaavn response provider=%s status=%s bytes=%s", name, status, len(body))
            if status != 200: continue
            payload = json.loads(body)
        except Exception as exc:
            LOGGER.warning("JioSaavn request failed provider=%s url=%s error=%r", name, url, exc)
            continue
        songs = payload.get("results") or payload.get("data") or []
        if isinstance(songs, dict): songs = songs.get("results") or songs.get("songs") or []
        LOGGER.info("JioSaavn parsed provider=%s result_count=%s payload_keys=%s", name, len(songs), list(payload)[:12])
        for song in songs:
            stream = _direct_url(song)
            if stream:
                LOGGER.info("JioSaavn playable result provider=%s title=%s", name, _value(song, "song", "title", "name"))
                return {"title": _value(song, "song", "title", "name") or query,
                        "url": stream, "webpage": _value(song, "url", "perma_url", "permaUrl")}
        LOGGER.warning("JioSaavn provider=%s returned no playable URL", name)
    return None


def _extract(source: str, label: str, reject_short: bool = False) -> Optional[dict]:
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl: info = ydl.extract_info(source, download=True)
    except Exception as exc:
        LOGGER.info("%s resolution failed: %s", label, exc); return None
    if not info: return None
    if info.get("entries"): info = next((entry for entry in info["entries"] if entry), None)
    if not info: return None
    path, duration = _downloaded_path(info), info.get("duration") or 0
    if reject_short and duration and duration < MIN_DURATION:
        if path:
            try: os.remove(path)
            except OSError: pass
        LOGGER.info("Ignoring %ss %s preview", duration, label); return None
    if not path: return None
    return {"title": info.get("title") or label, "url": path, "webpage": info.get("webpage_url")}


def resolve_track(query: str) -> dict:
    query = (query or "").strip()
    if not query: raise ResolveError("Empty music query")
    if query.startswith(("http://", "https://")):
        result = _extract(query, "direct URL")
    else:
        result = _jiosaavn_search(query)
        if result: return result
        result = _extract("scsearch1:" + query, "SoundCloud", reject_short=True)
        if result is None: result = _extract("ytsearch1:" + query, "YouTube", reject_short=True)
    if result is None: raise ResolveError("No full-length playable result found for {!r}".format(query))
    return result
