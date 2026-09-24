"""YouTube URL parsing, yt-dlp wrapper, and a small in-process TTL cache."""

from __future__ import annotations

import logging
from collections import OrderedDict
import os
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 256
MAX_SUBTITLE_BYTES = 20 * 1024 * 1024
# Extra attempts after an HTTP 429 on a caption download, and the sleep before each.
RETRY_DELAYS_429 = (2, 4, 8)
RATE_LIMITED_TRANSLATED = (
    "YouTube is rate-limiting machine-translated captions (HTTP 429). Download the original-language "
    "track instead (for example English en-orig) and translate it separately, or set "
    "YTDLP_COOKIES_CONTENT with fresh browser cookies."
)
RATE_LIMITED = (
    "YouTube returned HTTP 429 (too many requests). Wait a minute and try again, or set YTDLP_COOKIES_CONTENT."
)

# Tracks from these languages (primary subtag) follow the original-language one; mirrors the UI's PINNED_LANGS.
PINNED_LANGS = ("ko",)

INVALID_URL = "Please enter a valid YouTube video URL or 11-character video ID."
NO_SUBTITLES = "This video has no subtitles or auto-generated captions."
NO_SUBTITLES_POT = (
    "YouTube withheld the subtitles from this server (a PO token is required). The bundled token "
    "provider is not running or not reachable; check the container logs, or run the app on a residential network."
)


def no_subtitles_message(info: "VideoInfo") -> str:
    return NO_SUBTITLES_POT if info.pot_blocked else NO_SUBTITLES

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
    translated: bool = False  # auto track machine-translated by YouTube from another language

    def public(self) -> dict:
        return {"lang": self.lang, "name": self.name, "auto": self.auto, "translated": self.translated}


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    tracks: list[Track] = field(default_factory=list)
    upload_date: str | None = None  # ISO "YYYY-MM-DD"
    original_language: str | None = None  # spoken language, e.g. "en" or "en-US"
    webpage_url: str = field(init=False)  # always the canonical watch URL for video_id
    pot_blocked: bool = False  # True when yt-dlp dropped every track for lack of a PO token

    def __post_init__(self) -> None:
        self.webpage_url = f"https://www.youtube.com/watch?v={self.video_id}"

    def public(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "upload_date": self.upload_date,
            "url": self.webpage_url,
            "original_language": self.original_language,
            "tracks": [t.public() for t in self.tracks],
        }

    def find_track(self, lang: str, auto: bool) -> Track | None:
        return next((t for t in self.tracks if t.lang == lang and t.auto == auto), None)

    def header_meta(self, track: Track) -> dict:
        """Metadata for convert.render_header."""
        return {
            "title": self.title,
            "channel": self.channel,
            "duration": self.duration,
            "upload_date": self.upload_date,
            "url": self.webpage_url,
            "track_name": track.name,
            "track_lang": track.lang,
            "track_auto": track.auto,
        }


def safe_title(title: str, fallback: str) -> str:
    """Make a title safe for a filename: no reserved chars, collapsed spaces, max 80 chars."""
    name = "".join(ch for ch in title if unicodedata.category(ch)[0] != "C")
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:80].strip(" .")
    return name or fallback


def download_filename(info: VideoInfo, lang: str, auto: bool, fmt: str) -> tuple[str, str]:
    """(filename, ASCII fallback) used by /api/download, e.g. "Title.en.auto.txt"."""
    title = safe_title(info.title, info.video_id)
    lang_part = re.sub(r"[^A-Za-z0-9_-]", "", lang) or "sub"
    suffix = f".{lang_part}{'.auto' if auto else ''}.{fmt}"
    ascii_title = title if title.isascii() else info.video_id
    return title + suffix, ascii_title + suffix


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
    """Routes yt-dlp output into Python logging and keeps the warnings for inspection."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)
        log.warning("yt-dlp: %s", _ANSI_RE.sub("", msg))

    def error(self, msg: str) -> None:
        log.info(msg)


def warnings_mention_po_token(warnings: list[str]) -> bool:
    """True when yt-dlp said it dropped subtitles for lack of a PO token."""
    return any("po token" in w.lower() for w in warnings)


_COOKIE_HEADERS = ("# Netscape HTTP Cookie File", "# HTTP Cookie File")
_cookie_lock = threading.Lock()
_cookie_path: str | None = None


def _reset_cookie_cache() -> None:
    """Forget the cookies temp file (tests only; the file itself is left alone)."""
    global _cookie_path
    with _cookie_lock:
        _cookie_path = None


def _cookie_file_from_content(content: str) -> str:
    """Write YTDLP_COOKIES_CONTENT to a private temp file once per process; return its path."""
    global _cookie_path
    with _cookie_lock:
        if _cookie_path and os.path.isfile(_cookie_path):
            return _cookie_path
        if "\n" not in content and "\\n" in content:
            content = content.replace("\\n", "\n")  # a secrets UI flattened the newlines
        if not content.endswith("\n"):
            content += "\n"
        if not content.lstrip().startswith(_COOKIE_HEADERS):
            log.warning("YTDLP_COOKIES_CONTENT does not look like a Netscape cookies.txt; using it anyway.")
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", prefix="yt-cookies-", suffix=".txt", dir=tempfile.gettempdir(), delete=False
        ) as handle:
            os.chmod(handle.name, 0o600)
            handle.write(content)
        _cookie_path = handle.name
        return _cookie_path


def _cookie_file() -> str | None:
    """YTDLP_COOKIES (a path) wins; otherwise YTDLP_COOKIES_CONTENT (the file text) if set.

    yt-dlp rewrites the cookie file after each session, and mounted secret files
    (Render, Docker secrets) are read-only, so a path is copied to a private
    writable temp file once per process and yt-dlp gets the copy.
    """
    path = os.environ.get("YTDLP_COOKIES", "").strip()
    if path:
        if os.access(path, os.W_OK):
            return path
        try:
            with open(path, encoding="utf-8") as fh:
                return _cookie_file_from_content(fh.read())
        except OSError as exc:
            log.warning("YTDLP_COOKIES file %s could not be read (%s); continuing without cookies.", path, exc)
            return None
    content = os.environ.get("YTDLP_COOKIES_CONTENT", "")
    return _cookie_file_from_content(content) if content.strip() else None


def _ydl_options(logger: _QuietLogger | None = None) -> dict:
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
        "logger": logger or _QuietLogger(),
    }
    cookies = _cookie_file()
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
        line += " YouTube is blocking this server; add browser cookies (YTDLP_COOKIES file path or YTDLP_COOKIES_CONTENT), see README."
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


_NAME_NOISE_RE = re.compile(r"\(\s*(?:original|auto-generated)\s*\)|[-\u2013]\s*auto-generated\b", re.I)


def clean_track_name(name: str | None, lang: str) -> str:
    """Normalize YouTube's track name: "Original" means uploader-provided here.

    Drops "(Original)" and "(auto-generated)"; the auto flag and the lang code
    (for example "en-orig") carry that. Falls back to the language code.
    """
    return " ".join(_NAME_NOISE_RE.sub(" ", str(name or "")).split()).strip(" -") or lang


def _collect_tracks(mapping: dict | None, auto: bool) -> list[Track]:
    tracks = []
    for lang, entries in (mapping or {}).items():
        if lang == "live_chat":
            continue
        entry = _pick_entry(entries)
        if not entry:
            continue
        name = clean_track_name(entry.get("name"), lang)
        tracks.append(Track(lang=lang, name=name, auto=auto, url=entry["url"], ext=entry.get("ext") or "vtt"))
    return sorted(tracks, key=lambda t: t.lang)


def parse_upload_date(value) -> str | None:
    """Turn yt-dlp's "YYYYMMDD" into ISO "YYYY-MM-DD"; None when absent or invalid."""
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _primary_subtag(lang: str) -> str:
    """Lowercased primary language subtag: "en-US" -> "en", "en-orig" -> "en"."""
    return re.sub(r"-orig$", "", lang.strip(), flags=re.I).split("-")[0].lower()


def original_language(info: dict) -> str | None:
    """The video's spoken language.

    Order: the audio format yt-dlp marks as original (language_preference 10) or
    default (5), which is the only reliable signal on videos with several dubbed
    audio tracks; then yt-dlp's top-level "language"; then the "<lang>-orig" auto
    caption keys (English first when there are several, else the first one).
    """
    best_pref, best_lang = 0, None
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        lang = str(fmt.get("language") or "").strip()
        pref = fmt.get("language_preference")
        if lang and isinstance(pref, (int, float)) and pref >= 5 and pref > best_pref:
            best_pref, best_lang = pref, lang
    if best_lang:
        return best_lang
    language = str(info.get("language") or "").strip()
    if language:
        return language
    orig = [key[:-5] for key in (info.get("automatic_captions") or {}) if key.lower().endswith("-orig") and len(key) > 5]
    if not orig:
        return None
    english = [lang for lang in orig if _primary_subtag(lang) == "en"]
    return english[0] if english else orig[0]


def _mark_translated(tracks: list[Track], original: str | None) -> None:
    """Auto tracks in another language than the original are translated server-side by YouTube."""
    if not original:
        return
    base = _primary_subtag(original)
    for track in tracks:
        orig_track = track.lang.lower().endswith("-orig")
        track.translated = track.auto and not orig_track and _primary_subtag(track.lang) != base


def _order_tracks(tracks: list[Track], original: str | None) -> list[Track]:
    """First the original-language track, then PINNED_LANGS in order, then the rest by lang."""
    rest = sorted(tracks, key=lambda t: t.lang)
    out: list[Track] = []
    base = _primary_subtag(original) if original else ""
    first = next((t for t in rest if base and _primary_subtag(t.lang) == base), None)
    if first:
        out.append(first)
    for pinned in PINNED_LANGS:
        out += [t for t in rest if t not in out and _primary_subtag(t.lang) == pinned]
    return out + [t for t in rest if t not in out]


def downloadable_tracks(info: VideoInfo) -> list[Track]:
    """The tracks worth offering (same rule as the web UI).

    All manual tracks, then every "<lang>-orig" auto track; without any -orig
    track, the one auto track in the original language (else the first
    non-translated auto track). Machine-translated auto tracks are left out
    because YouTube rate-limits them.
    """
    manual = _order_tracks([t for t in info.tracks if not t.auto], info.original_language)
    autos = [t for t in info.tracks if t.auto and not t.translated]
    orig = [t for t in autos if t.lang.lower().endswith("-orig")]
    if orig:
        return manual + _order_tracks(orig, info.original_language)
    base = _primary_subtag(info.original_language) if info.original_language else ""
    pick = next((t for t in autos if base and _primary_subtag(t.lang) == base), autos[0] if autos else None)
    return manual + ([pick] if pick else [])


def recommended_track(info: VideoInfo) -> Track | None:
    """The original-language manual track, else the first manual, else the first downloadable auto track."""
    tracks = downloadable_tracks(info)
    base = _primary_subtag(info.original_language) if info.original_language else ""
    manual = [t for t in tracks if not t.auto]
    same = next((t for t in manual if base and _primary_subtag(t.lang) == base), None)
    return same or (manual[0] if manual else None) or (tracks[0] if tracks else None)


def normalize_info(video_id: str, info: dict) -> VideoInfo:
    duration = info.get("duration")
    original = original_language(info)
    tracks = _collect_tracks(info.get("subtitles"), False) + _collect_tracks(info.get("automatic_captions"), True)
    _mark_translated(tracks, original)
    return VideoInfo(
        video_id=video_id,
        title=str(info.get("title") or video_id),
        channel=info.get("channel") or info.get("uploader"),
        duration=int(duration) if isinstance(duration, (int, float)) else None,
        thumbnail=info.get("thumbnail"),
        tracks=tracks,
        upload_date=parse_upload_date(info.get("upload_date")) or parse_upload_date(info.get("release_date")),
        original_language=original,
    )


_cache: dict[str, tuple[float, VideoInfo]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _subtitle_cache.clear()


# Raw caption text per track URL, so a second action on the same track (preview, then download, then a
# summary prompt) does not download it from YouTube again. Same TTL as the video info that holds the URLs.
SUBTITLE_CACHE_MAX_ENTRIES = 64
_subtitle_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _subtitle_cache_get(url: str) -> str | None:
    with _cache_lock:
        hit = _subtitle_cache.get(url)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _subtitle_cache.pop(url, None)
        return None


def _subtitle_cache_put(url: str, text: str) -> None:
    now = time.monotonic()
    with _cache_lock:
        for key in [k for k, (exp, _) in _subtitle_cache.items() if exp <= now]:
            del _subtitle_cache[key]
        while len(_subtitle_cache) >= SUBTITLE_CACHE_MAX_ENTRIES:
            del _subtitle_cache[next(iter(_subtitle_cache))]
        _subtitle_cache[url] = (now + CACHE_TTL_SECONDS, text)


def _cache_get(video_id: str) -> VideoInfo | None:
    with _cache_lock:
        hit = _cache.get(video_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _cache.pop(video_id, None)
        return None


def cached_infos() -> list[VideoInfo]:
    """Unexpired cached videos, most recently fetched first."""
    now = time.monotonic()
    with _cache_lock:
        return [info for exp, info in reversed(list(_cache.values())) if exp > now]


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

    logger = _QuietLogger()
    try:
        with yt_dlp.YoutubeDL(_ydl_options(logger)) as ydl:
            info = ydl.extract_info(canonical_url(video_id), download=False)
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        raise YoutubeError(_one_line(exc)) from exc
    if not isinstance(info, dict):
        raise YoutubeError("yt-dlp returned no video information.")
    result = normalize_info(video_id, info)
    if not result.tracks and warnings_mention_po_token(logger.warnings):
        result.pot_blocked = True
        log.warning("%s: subtitles withheld pending a PO token; is the bgutil provider running?", video_id)
        return result  # not cached, so a recovered provider is picked up on the next request
    _cache_put(video_id, result)
    return result


POT_PROVIDER_URL = os.environ.get("POT_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")


def pot_provider_status(timeout: float = 1.0) -> dict:
    """Ping the bgutil PO token provider; {"reachable": bool, "version": str | None}."""
    import json
    import urllib.request

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{POT_PROVIDER_URL}/ping", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        return {"reachable": True, "version": data.get("version")}
    except Exception:
        return {"reachable": False, "version": None}


def _is_rate_limited(exc: BaseException) -> bool:
    """HTTP 429 from yt-dlp's or urllib's HTTPError (also when wrapped), or "429" in the message."""
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            break
        if 429 in (getattr(seen, "status", None), getattr(seen, "code", None)):
            return True
        seen = seen.__cause__ or seen.__context__
    return bool(re.search(r"\b429\b", str(exc)))


def _download_caption(url: str) -> bytes:
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
        return ydl.urlopen(url).read(MAX_SUBTITLE_BYTES + 1)


def fetch_subtitle_text(track: Track) -> str:
    """Download a caption file through yt-dlp so its headers, proxy, and cookies apply (blocking).

    HTTP 429 is retried after each delay in RETRY_DELAYS_429; other errors fail at once.
    Successful downloads are cached for CACHE_TTL_SECONDS per caption URL.
    """
    cached = _subtitle_cache_get(track.url)
    if cached is not None:
        return cached
    delays = list(RETRY_DELAYS_429)
    while True:
        try:
            data = _download_caption(track.url)
            break
        except Exception as exc:
            if not _is_rate_limited(exc):
                raise YoutubeError(_one_line(exc)) from exc
            if not delays:
                raise YoutubeError(RATE_LIMITED_TRANSLATED if track.translated else RATE_LIMITED) from exc
            log.info("HTTP 429 for %s subtitles; retrying", track.lang)
            time.sleep(delays.pop(0))
    if len(data) > MAX_SUBTITLE_BYTES:
        raise YoutubeError("Subtitle file is too large.")
    text = data.decode("utf-8", "replace")
    _subtitle_cache_put(track.url, text)
    return text
