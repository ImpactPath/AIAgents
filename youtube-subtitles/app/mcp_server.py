"""MCP server (Model Context Protocol) exposing the subtitle downloader as two tools.

Mounted by app.main at /mcp (streamable HTTP, stateless, JSON responses), so
Claude and ChatGPT connectors can call it. The tools reuse the same cached
yt-dlp lookup, track rules, and converter as the REST API.
"""

from __future__ import annotations

import inspect
from typing import Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from typing_extensions import TypedDict  # pydantic needs this TypedDict on Python < 3.12

from app import __version__, youtube
from app.convert import convert, format_duration

INSTRUCTIONS = (
    "Fetch YouTube subtitles. Call get_video_info first to see the downloadable tracks, then "
    "get_subtitles. Machine-translated YouTube captions are rate-limited and hidden; download the "
    "original language and translate the text yourself if another language is needed."
)

server = MCPServer(
    name="youtube-subtitles",
    title="YouTube Subtitle Downloader",
    version=__version__,
    instructions=INSTRUCTIONS,
)


class TrackOut(TypedDict):
    lang: str
    name: str
    auto: bool
    translated: bool


class RecommendedOut(TypedDict):
    lang: str
    auto: bool


class VideoInfoOut(TypedDict):
    video_id: str
    title: str
    channel: str | None
    duration_seconds: int | None
    duration: str | None
    published: str | None
    url: str
    original_language: str | None
    tracks: list[TrackOut]
    recommended: RecommendedOut | None


def _register(fn, *, structured: bool):
    """Register a tool; the dedented docstring becomes its description."""
    server.add_tool(fn, description=inspect.cleandoc(fn.__doc__ or ""), structured_output=structured)
    return fn


def build_http_app() -> Starlette:
    """Create a fresh streamable HTTP transport (and session manager) for one app lifespan.

    A session manager can only run once, so app.main calls this on every
    startup. Host/Origin checks are off: the app runs behind proxies (Hugging
    Face, Render) with arbitrary Host headers, and /mcp has its own API key guard.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


async def _load(url: str) -> youtube.VideoInfo:
    video_id = youtube.parse_video_id(url)
    if not video_id:
        raise ToolError(youtube.INVALID_URL)
    try:
        info = await anyio.to_thread.run_sync(youtube.fetch_info, video_id)
    except youtube.YoutubeError as exc:
        raise ToolError(f"Could not fetch video info: {exc}") from exc
    if not info.tracks:
        raise ToolError(youtube.no_subtitles_message(info))
    return info


def _pick_track(info: youtube.VideoInfo, lang: str | None, auto: bool | None) -> youtube.Track:
    if not lang:
        track = youtube.recommended_track(info)
        if not track:
            raise ToolError("This video has only machine-translated captions, which YouTube rate-limits.")
        return track
    if auto is not None:
        track = info.find_track(lang, auto)
    else:
        track = info.find_track(lang, False) or info.find_track(lang, True) or info.find_track(f"{lang}-orig", True)
    if not track:
        kind = "" if auto is None else ("auto-generated " if auto else "manual ")
        raise ToolError(f"No {kind}subtitle track for language '{lang}'. Call get_video_info to list the tracks.")
    return track


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars + 1)
    kept = text[: cut if cut > 0 else max_chars]
    return f"{kept}\n[truncated: {len(text) - len(kept)} more characters]"


async def get_video_info(url: str) -> VideoInfoOut:
    """Look up a YouTube video and list its downloadable subtitle tracks.

    `url` is a YouTube URL (watch, youtu.be, shorts, live, embed) or an 11-character video id.
    Returns title, channel, duration, published date (YYYY-MM-DD), original_language, `tracks`
    (manual uploader tracks first, then original-language auto captions such as "en-orig"; each
    with lang, name, auto, translated) and `recommended` ({lang, auto} to pass to get_subtitles, or
    null). Machine-translated auto captions are not listed because YouTube rate-limits them.
    """
    info = await _load(url)
    tracks = youtube.downloadable_tracks(info)
    rec = youtube.recommended_track(info)
    return VideoInfoOut(
        video_id=info.video_id,
        title=info.title,
        channel=info.channel,
        duration_seconds=info.duration,
        duration=format_duration(info.duration) or None,
        published=info.upload_date,
        url=info.webpage_url,
        original_language=info.original_language,
        tracks=[TrackOut(**t.public()) for t in tracks],
        recommended=RecommendedOut(lang=rec.lang, auto=rec.auto) if rec else None,
    )


async def get_subtitles(
    url: str,
    lang: str | None = None,
    auto: bool | None = None,
    fmt: Literal["txt", "srt", "vtt"] = "txt",
    layout: Literal["paragraphs", "sentences", "cues"] = "paragraphs",
    include_header: bool = True,
    max_chars: int = 200000,
) -> str:
    """Download the subtitles of a YouTube video as text.

    `url`: YouTube URL or 11-character video id.
    `lang`: track language code from get_video_info (for example "en", "ko", "en-orig"). Default: the
    recommended track (the original-language uploader track, else the first uploader track, else the
    original-language auto captions).
    `auto`: true for auto-generated captions, false for uploader subtitles. Default (null): prefer the
    uploader track in `lang`, else the auto track in `lang`, else the auto track "<lang>-orig".
    `fmt`: "txt" (default), "srt" or "vtt". `layout` (txt only): "paragraphs" (default), "sentences"
    (one per line) or "cues" (original caption lines).
    `include_header` (default true): start with title, channel, duration, published date, URL and track.
    `max_chars` (default 200000): longer output is cut at a line boundary and ends with
    "[truncated: N more characters]".
    Machine-translated auto captions (another language than the video's) are rate-limited by YouTube
    (HTTP 429); fetch the original language instead and translate the text yourself.
    """
    if max_chars < 1:
        raise ToolError("max_chars must be a positive number.")
    info = await _load(url)
    track = _pick_track(info, (lang or "").strip() or None, auto)
    try:
        raw = await anyio.to_thread.run_sync(youtube.fetch_subtitle_text, track)
    except youtube.YoutubeError as exc:
        raise ToolError(f"Could not download subtitles: {exc}") from exc
    header = info.header_meta(track) if include_header else None
    body = convert(raw, fmt, layout, header=header)  # layout is ignored for srt/vtt
    if not body.strip() or body.strip() == "WEBVTT":
        raise ToolError("The subtitle track was empty or could not be parsed.")
    return _truncate(body, max_chars)


_register(get_video_info, structured=True)
_register(get_subtitles, structured=False)  # plain text; a structured copy would double the payload
