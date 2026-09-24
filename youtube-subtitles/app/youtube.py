"""YouTube URL parsing, yt-dlp wrapper, and a small in-process TTL cache."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 256
MAX_SUBTITLE_BYTES = 20 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ID_PREFIX_RE = re.compile(r"^([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_PREFIXES = {"shorts", "live", "embed", "v"}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class YoutubeError(Exception):
    """yt-dlp failed (private, removed, geo-blocked, bot check, network). Maps to HTTP 502."""


@dataclass
class Track:
    lang: str
    name: str
    auto: bool
    url: str = field(repr=False)
    ext: str = "vtt"

    def public(self) -> dict:
        return {"lang": self.lang, "name": self.name, "auto": self.auto}


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    tracks: list[Track] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "tracks": [t.public() for t in self.tracks],
        }

    def find_track(self, lang: str, auto: bool) -> Track | None:
        return next((t for t in self.tracks if t.lang == lang and t.auto == auto), None)


def parse_video_id(text: str | None) -> str | None:
    """Return the 11-char video id from a YouTube URL or bare id, or None."""
    if not text:
        return None
    text = text.strip()
    if _ID_RE.match(text):
        return text
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    candidate = ""
    if host in _SHORT_HOSTS:
        candidate = parts[0] if parts else ""
    elif host in _YT_HOSTS:
        if parts[:1] == ["watch"]:
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in _PATH_PREFIXES:
            candidate = parts[1]
    match = _ID_PREFIX_RE.match(candidate.strip())
    return match.group(1) if match else None


def canonical_url(video_id: str) -> str:
    """Always rebuild the URL from a parsed id; user URLs never reach yt-dlp (SSRF guard)."""
    if not _ID_RE.match(video_id):
        raise ValueError("invalid video id")
    return f"https://www.youtube.com/watch?v={video_id}"


class _QuietLogger:
    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.debug(msg)

    def error(self, msg: str) -> None:
        log.info(msg)


def _ydl_options() -> dict:
    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["all"],
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 20,
        "logger": _QuietLogger(),
    }
    cookies = os.environ.get("YTDLP_COOKIES", "").strip()
    if cookies:
        opts["cookiefile"] = cookies
    proxy = os.environ.get("YTDLP_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy
    return opts


def _one_line(exc: BaseException) -> str:
    """Trim a yt-dlp error message to a single readable line."""
    raw = _ANSI_RE.sub("", str(exc) or exc.__class__.__name__)
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "Unknown error")
    line = re.sub(r"^ERROR:\s*", "", line)
    # Drop yt-dlp boilerplate that is noise for end users.
    line = re.split(r";?\s*please report this issue", line, maxsplit=1, flags=re.I)[0]
    line = re.split(r"\s*\(caused by ", line, maxsplit=1)[0].strip()
    if len(line) > 300:
        line = line[:297] + "..."
    if "Sign in to confirm" in raw:
        line += " A cookies file may be needed (set YTDLP_COOKIES to a cookies.txt path)."
    return line


def _pick_entry(entries: list) -> dict | None:
    """Prefer vtt, then srt, then the first format our parser can read."""
    usable = [e for e in entries or [] if isinstance(e, dict) and e.get("url")]
    for wanted in ("vtt", "srt"):
        for entry in usable:
            if entry.get("ext") == wanted:
                return entry
    for entry in usable:
        ext = str(entry.get("ext") or "")
        if not (ext.startswith("json") or ext.startswith("srv") or ext == "ttml"):
            return entry
    return None


def _collect_tracks(mapping: dict | None, auto: bool) -> list[Track]:
    tracks = []
    for lang, entries in (mapping or {}).items():
        if lang == "live_chat":
            continue
        entry = _pick_entry(entries)
        if not entry:
            continue
        name = str(entry.get("name") or lang)
        if auto and "auto-generated" not in name.lower():
            name += " (auto-generated)"
        tracks.append(Track(lang=lang, name=name, auto=auto, url=entry["url"], ext=entry.get("ext") or "vtt"))
    return sorted(tracks, key=lambda t: t.lang)


def normalize_info(video_id: str, info: dict) -> VideoInfo:
    duration = info.get("duration")
    return VideoInfo(
        video_id=video_id,
        title=str(info.get("title") or video_id),
        channel=info.get("channel") or info.get("uploader"),
        duration=int(duration) if isinstance(duration, (int, float)) else None,
        thumbnail=info.get("thumbnail"),
        tracks=_collect_tracks(info.get("subtitles"), False) + _collect_tracks(info.get("automatic_captions"), True),
    )


_cache: dict[str, tuple[float, VideoInfo]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_get(video_id: str) -> VideoInfo | None:
    with _cache_lock:
        hit = _cache.get(video_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _cache.pop(video_id, None)
        return None


def _cache_put(video_id: str, info: VideoInfo) -> None:
    now = time.monotonic()
    with _cache_lock:
        for key in [k for k, (exp, _) in _cache.items() if exp <= now]:
            del _cache[key]
        while len(_cache) >= CACHE_MAX_ENTRIES:
            del _cache[next(iter(_cache))]
        _cache[video_id] = (now + CACHE_TTL_SECONDS, info)


def fetch_info(video_id: str) -> VideoInfo:
    """Extract metadata and subtitle tracks (blocking). Cached for 10 minutes per video id."""
    cached = _cache_get(video_id)
    if cached:
        return cached
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
            info = ydl.extract_info(canonical_url(video_id), download=False)
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        raise YoutubeError(_one_line(exc)) from exc
    if not isinstance(info, dict):
        raise YoutubeError("yt-dlp returned no video information.")
    result = normalize_info(video_id, info)
    _cache_put(video_id, result)
    return result


def fetch_subtitle_text(track: Track) -> str:
    """Download a caption file through yt-dlp so its headers, proxy, and cookies apply (blocking)."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
            data = ydl.urlopen(track.url).read(MAX_SUBTITLE_BYTES + 1)
    except Exception as exc:
        raise YoutubeError(_one_line(exc)) from exc
    if len(data) > MAX_SUBTITLE_BYTES:
        raise YoutubeError("Subtitle file is too large.")
    return data.decode("utf-8", "replace")
