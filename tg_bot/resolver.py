"""Resolve music queries through JioSaavn, SoundCloud, then YouTube."""

import glob
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from typing import Optional

import yt_dlp

LOGGER = logging.getLogger(__name__)
MIN_DURATION = 45

_YDL_COMMON = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "noprogress": True, "no_warnings": True, "nocheckcertificate": True, "socket_timeout": 15, "retries": 2, "outtmpl": "/tmp/mp_%(id)s.%(ext)s"}
_YOUTUBE_SPOOF = {"extractor_args": {"youtube": {"player_client": ["android_creator", "mweb", "android", "ios"]}}}
GATED_HOSTS = ("youtube.com", "youtu.be", "googlevideo.com", "ggpht.com", "ytimg.com")

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
        for path in glob.glob("/tmp/mp_{}.*".format(track_id)):
            if not path.endswith(".part"): return path
    return None

def _is_gated_host(hostname: str) -> bool:
    hostname = (hostname or "").lower()
    return any(hostname == host or hostname.endswith("." + host) for host in GATED_HOSTS)

def _clean_query(query: str) -> str:
    query = re.sub(r"^[\s\-_/|:]+|[\s\-_/|:]+$", "", query.lower())
    query = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", query)
    query = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    return re.sub(r"\s+", " ", query).strip()

def _query_variants(query: str):
    clean = _clean_query(query); variants = [clean]; words = clean.split()
    if len(words) > 2:
        variants += [" ".join(words[-3:]), " ".join(words[-2:])]
    for source, target in (("tlahum", "tilahun"), ("tlahun", "tilahun"), ("gesese", "gessesse"), ("gessese", "gessesse")):
        if source in clean: variants.insert(1, clean.replace(source, target))
    return list(dict.fromkeys(v for v in variants if v))

def _value(song: dict, *keys: str):
    for key in keys:
        value = song.get(key)
        if value not in (None, ""): return value
    return ""

def _score(info: dict, query: str) -> float:
    title = str(info.get("title") or "").lower(); artist = str(_value(info, "uploader", "uploader_id", "artist", "creator", "channel") or "").lower()
    tokens = re.findall(r"[a-z0-9]+", query.lower()); score = sum(20 for token in tokens if token in title or token in artist)
    if tokens and all(token in title or token in artist for token in tokens): score += 30
    if any(term in title for term in ("karaoke", "instrumental", "tribute", "cover", "remix")): score -= 100
    return score

def _search_jiosaavn(query: str) -> Optional[dict]:
    url = "https://www.jiosaavn.com/api.php?" + urllib.parse.urlencode({"__call": "search.getResults", "_format": "json", "_marker": 0, "query": query, "n": 5})
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=12) as response: payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        LOGGER.warning("JioSaavn search failed: %s", exc); return None
    entries = payload if isinstance(payload, list) else (payload.get("results") or []) if isinstance(payload, dict) else []
    for entry in entries:
        if not isinstance(entry, dict): continue
        media = entry.get("media_url") or entry.get("download_url") or ""
        if not isinstance(media, str) or not media.startswith("https://"): continue
        try: duration = int(entry.get("duration") or 0)
        except (TypeError, ValueError): duration = 0
        if duration and duration < MIN_DURATION: continue
        title = entry.get("title") or query
        LOGGER.info("JioSaavn CDN direct title=%s artist=%s", title, entry.get("singers") or entry.get("artist") or "")
        return {"title": title, "url": media, "webpage": entry.get("perma_url")}
    return None

def _search_ytdlp(extractor: str, query: str, count: int = 5) -> dict:
    opts = dict(_YDL_COMMON)
    if extractor.startswith("ytsearch"): opts.update(_YOUTUBE_SPOOF)
    search_source = "{}:{}".format(extractor, query)
    LOGGER.info("%s candidate search query=%r count=%d", extractor, query, count)
    with yt_dlp.YoutubeDL(opts) as ydl:
        try: listing = ydl.extract_info(search_source, download=False)
        except Exception as exc: raise ResolveError("yt-dlp %s search failed for %r: %s" % (extractor, query, exc)) from exc
        entries = [entry for entry in (listing.get("entries") or []) if entry][:count] if listing else []
        if not entries and listing: entries = [listing]
        entries.sort(key=lambda entry: _score(entry, query), reverse=True)
        errors = []
        for index, candidate in enumerate(entries, 1):
            title = candidate.get("title") or query; uploader = candidate.get("uploader") or candidate.get("channel") or ""; duration = candidate.get("duration") or 0; score = _score(candidate, query)
            LOGGER.info("%s candidate=%d title=%s artist=%s duration=%s score=%.2f", extractor, index, title, uploader, duration, score)
            if duration and duration < MIN_DURATION: errors.append("%s is only %ss" % (title, duration)); continue
            source = candidate.get("webpage_url") or candidate.get("url") or candidate.get("original_url")
            if not source: errors.append("%s has no source URL" % title); continue
            try:
                info = ydl.extract_info(source, download=True)
                if info and info.get("entries"): info = next((item for item in info["entries"] if item), None)
                path = _downloaded_path(info or {})
                if not path: raise ResolveError("download did not materialize")
                actual_duration = (info.get("duration") or duration) if info else duration
                if actual_duration and actual_duration < MIN_DURATION:
                    os.remove(path); raise ResolveError("only %ss preview" % actual_duration)
                return {"title": info.get("title", title) if info else title, "url": path, "webpage": info.get("webpage_url", source) if info else source}
            except Exception as exc:
                errors.append("%s: %s" % (title, exc)); LOGGER.warning("%s candidate=%d failed; trying next: %s", extractor, index, exc)
        raise ResolveError("no playable %s candidate for %r (%s)" % (extractor, query, "; ".join(errors)))

def _search_soundcloud(query: str) -> dict:
    errors = []
    for variant in _query_variants(query):
        try: return _search_ytdlp("scsearch5", variant, count=5)
        except ResolveError as exc: errors.append(str(exc)); LOGGER.warning("SoundCloud variant failed query=%r: %s", variant, exc)
    raise ResolveError("SoundCloud exhausted all query variants: %s" % " | ".join(errors))

def resolve_track(query: str) -> dict:
    query = (query or "").strip()
    if not query: raise ResolveError("Empty music query")
    if query.startswith(("http://", "https://")):
        if _is_gated_host(urllib.parse.urlparse(query).hostname or ""): raise ResolveError("YouTube links are blocked from this host")
        return {"title": query, "url": query, "webpage": query}
    errors = []
    for variant in _query_variants(query):
        tier = _search_jiosaavn(variant)
        if tier: return tier
    try: return _search_soundcloud(query)
    except ResolveError as exc: errors.append(str(exc))
    for variant in _query_variants(query):
        try: return _search_ytdlp("ytsearch5", variant, count=5)
        except ResolveError as exc: errors.append(str(exc))
    raise ResolveError("No free stream resolved for %r -- %s" % (query, " | ".join(errors)))
