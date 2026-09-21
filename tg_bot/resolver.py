"""Resolver: query -> a playable stream.

Cascade so /play starts INSTANTLY and never forces a disk download when a
direct CDN stream URL exists:

  Tier 1  JioSaavn public search -> direct https CDN media_url (no disk).
  Tier 2  SoundCloud scsearch1   -> local download, materialize seam.
  Tier 3  YouTube ytsearch1      -> local download with Android/iOS client
                                    spoof (the datacenter-IP preview bypass).

Only a literal http:// or https:// input is treated as a direct URL. Everything
else is a text search. A preview rejected at one tier falls through to the next
instead of killing the whole /play. Each tier failure is recorded and the next
provider gets the byte; only "all three refused" raises.
"""

import glob
import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

import yt_dlp

LOGGER = logging.getLogger(__name__)

_YDL_COMMON = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "noprogress": True,
    "no_warnings": True,
    "noprogress": True,
    "nocheckcertificate": True,
    "socket_timeout": 15,
    "retries": 3,
    "outtmpl": "/tmp/mp_%(id)s.%(ext)s",
}

# The android/ios players return the real full CDN stream -- this is the
# "cut out after 20s" bypass for Render's datacenter IP on YouTube.
_YOUTUBE_SPOOF = {
    "extractor_args": {
        "youtube": {"player_client": ["android", "ios"]},
    },
}

GATED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "googlevideo.com",
    "ggpht.com",
    "ytimg.com",
)

MIN_DURATION = 45

_JIOSAAVN_SEARCH = (
    "https://www.jiosaavn.com/api.php"
    "?__call=search.getResults"
    "&_format=json"
    "&_marker=0"
    "&query={query}"
    "&n=5"
)


class ResolveError(Exception):
    pass


def _is_gated_host(hostname: str) -> bool:
    hostname = (hostname or "").lower()
    return any(
        hostname == host or hostname.endswith("." + host) for host in GATED_HOSTS
    )


def _is_gated(url: str) -> bool:
    hostname = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return _is_gated_host(hostname)


def _downloaded_path(info: dict) -> Optional[str]:
    for entry in info.get("requested_downloads") or []:
        path = entry.get("filepath") or entry.get("_filename")
        if path and os.path.exists(path):
            return path
    path = info.get("filepath") or info.get("_filename")
    if path and os.path.exists(path):
        return path
    # FIX: on datacenter IPs (Render), the file materializes straight into the
    # outtmpl and the info dict never carries filepath/_filename. The track id
    # is the one byte we always hold -- glob the output template.
    track_id = info.get("id")
    if track_id:
        for matched in glob.glob("/tmp/mp_{}.*".format(track_id)):
            if not matched.endswith(".part"):
                return matched
    return None


def _search_jiosaavn(query: str) -> Optional[dict]:
    """Tier 1: JioSaavn public search API -> direct CDN https media URL.

    No yt-dlp, no disk write -- the media_url streams straight into the voice
    call the moment /play fires. Returns None (not an exception) so the cascade
    falls through instead of dying."""
    encoded = urllib.parse.quote(query)
    url = _JIOSAAVN_SEARCH.format(query=encoded)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        LOGGER.warning("JioSaavn search failed (%s); falling to SoundCloud", exc)
        return None

    if not isinstance(payload, list):
        payload = (payload.get("results") or []) if isinstance(payload, dict) else []

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        media = entry.get("media_url") or ""
        if not media.startswith("https://"):
            continue
        try:
            duration = int(entry.get("duration") or 0) or 0
        except (TypeError, ValueError):
            duration = 0
        if duration and duration < MIN_DURATION:
            continue
        title = entry.get("title") or query
        LOGGER.info("JioSaavn CDN direct: %r", title)
        return {
            "title": title,
            "url": media,
            "webpage": entry.get("perma_url"),
        }
    return None


def _search_ytdlp(extractor: str, query: str) -> dict:
    """Tier 2/3: yt-dlp search + download -> one materialized local file.

    extractor is 'scsearch1' (SoundCloud) or 'ytsearch1' (YouTube). The YouTube
    path spoofs the Android/iOS player client so a datacenter IP gets the real
    full CDN stream, not the 20s gated preview."""
    opts = dict(_YDL_COMMON)
    if extractor == "ytsearch1":
        opts.update(_YOUTUBE_SPOOF)

    source = "{}:{}".format(extractor, query)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(source, download=True)
        except Exception as exc:
            raise ResolveError(
                "yt-dlp {extractor} failed for {query!r}: {exc}".format(
                    extractor=extractor, query=query, exc=exc
                )
            ) from exc
    if not info:
        raise ResolveError(
            "Nothing resolved on {extractor} for {query!r}".format(
                extractor=extractor, query=query
            )
        )
    if info.get("entries"):
        info = info["entries"][0]

    title = info.get("title") or query
    duration = info.get("duration") or 0
    local_path = _downloaded_path(info)
    if not local_path:
        raise ResolveError(
            "Download did not materialize on {extractor} for {title!r}".format(
                extractor=extractor, title=title
            )
        )
    if duration and duration < MIN_DURATION:
        try:
            os.remove(local_path)
        except OSError:
            pass
        raise ResolveError(
            "Only a {:d}s preview is available for {title!r} -- the full track "
            "is gated from this IP.".format(duration, title=title)
        )

    return {"title": title, "url": local_path, "webpage": info.get("webpage_url")}


def resolve_track(query: str) -> dict:
    """One byte in, one stream out, tier-cascade.

    Tier 1 JioSaavn (direct CDN, no disk) -> Tier 2 SoundCloud -> Tier 3
    YouTube. Direct URLs (http:// or https://) pass through unchanged unless
    they are gated hosts."""
    query = (query or "").strip()
    if not query:
        raise ResolveError("Empty music query")

    is_url = query.startswith(("http://", "https://"))
    if is_url:
        if _is_gated_host((urllib.parse.urlparse(query).hostname or "")):
            raise ResolveError(
                "YouTube links are blocked from this host. Try a SoundCloud or "
                "Bandcamp link, or search by name."
            )

        # Direct non-gated link: pass the byte straight through to the player.
        return {
            "title": query,
            "url": query,
            "webpage": query,
        }

    # Tier 1 -- JioSaavn direct CDN (instant, no disk).
    tier = _search_jiosaavn(query)
    if tier is not None:
        return tier

    # Tier 2/3 -- cascade search-and-materialize.
    errors = []
    for extractor in ("scsearch1", "ytsearch1"):
        try:
            return _search_ytdlp(extractor, query)
        except ResolveError as exc:
            errors.append(str(exc))
            LOGGER.warning("cascade %s -> %s: %s", extractor, "ytsearch1" if extractor == "scsearch1" else "END", exc)

    raise ResolveError(
        "No free stream resolved for {query!r} -- JioSaavn, SoundCloud and "
        "YouTube all refused. Details: {reasons}".format(
            query=query, reasons=" | ".join(errors)
        )
    )
