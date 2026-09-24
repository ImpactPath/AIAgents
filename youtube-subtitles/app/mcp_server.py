"""MCP server (Model Context Protocol) exposing the subtitle downloader as three tools.

Mounted by app.main at /mcp (streamable HTTP, stateless, JSON responses), so
Claude and ChatGPT connectors can call it. The tools reuse the same cached
yt-dlp lookup, track rules, and converter as the REST API.
"""

from __future__ import annotations

import inspect
import os
from typing import Literal
from urllib.parse import urlencode

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from typing_extensions import TypedDict  # pydantic needs this TypedDict on Python < 3.12

from app import __version__, youtube
from app.convert import convert, format_duration

INSTRUCTIONS = (
    "Fetch YouTube subtitles. When the user shares a YouTube link without saying what they want, call "
    "get_video_info only, then show a short menu in the user's language and wait: (a) the subtitle tracks, "
    "recommended first; (b) format TXT, SRT, VTT (default TXT); (c) text layout for TXT: paragraphs "
    "(default), sentences, cues; (d) what to do: download link, summary, translation, key points. Only call "
    "get_subtitles or get_download_link after the user chooses, or when the user's request already makes "
    "the choice clear (for example 'summarize this video' means fetch the recommended track as TXT "
    "paragraphs and summarize). To give the user a file, call get_download_link and show its download_url "
    "as a clickable link; it does not fetch the subtitles. Machine-translated YouTube captions are "
    "rate-limited and hidden; download the original language and translate the text yourself if another "
    "language is needed."
)

MENU_HINT = (
    "If the user gave no instruction, present the tracks (recommended first), formats, layouts and actions "
    "below as a short menu in the user's language and wait for a choice before calling get_subtitles or "
    "get_download_link."
)
OPTIONS = {
    "formats": [
        {"id": "txt", "label": "Plain text"},
        {"id": "srt", "label": "SubRip (SRT), with timestamps"},
        {"id": "vtt", "label": "WebVTT (VTT), with timestamps"},
    ],
    "layouts": [
        {"id": "paragraphs", "label": "Paragraphs (sentences joined, blank line between paragraphs)"},
        {"id": "sentences", "label": "One sentence per line"},
        {"id": "cues", "label": "Original caption cues"},
    ],
    "actions": ["download_link", "summary", "translation", "key_points"],
}
UNCONFIGURED_NOTE = (
    "The server's public URL is not configured (set PUBLIC_BASE_URL), so this is a path relative to the "
    "server's address; prefix it with the host the user opens the web app on."
)
READY_NOTE = "Open the link in a browser to save the file; the web app needs no login."

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


class OptionOut(TypedDict):
    id: str
    label: str


class OptionsOut(TypedDict):
    formats: list[OptionOut]
    layouts: list[OptionOut]
    actions: list[str]


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
    options: OptionsOut
    menu_hint: str


class DownloadTrackOut(TypedDict):
    lang: str
    name: str
    auto: bool


class DownloadLinkOut(TypedDict):
    download_url: str
    web_app_url: str
    filename: str
    track: DownloadTrackOut
    note: str


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


def _base_url(ctx: Context | None) -> str:
    """Public base URL without a trailing slash, or "" when unknown.

    PUBLIC_BASE_URL, else RENDER_EXTERNAL_URL, else the Host (or X-Forwarded-Host) header and scheme
    (X-Forwarded-Proto first) of the HTTP request carrying this tool call.
    """
    base = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if base:
        return base.rstrip("/")
    try:
        request = ctx.request_context.request if ctx is not None else None
    except (ValueError, RuntimeError):  # no active request (stdio or a direct call)
        request = None
    headers = getattr(request, "headers", None)
    if not headers:
        return ""
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    if not host:
        return ""
    scheme = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        url = getattr(request, "url", None)
        scheme = getattr(url, "scheme", "") if url is not None else ""
        scheme = scheme if scheme in ("http", "https") else "https"
    return f"{scheme}://{host}"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars + 1)
    kept = text[: cut if cut > 0 else max_chars]
    return f"{kept}\n[truncated: {len(text) - len(kept)} more characters]"


async def get_video_info(url: str) -> VideoInfoOut:
    """Look up a YouTube video and list its downloadable subtitle tracks.

    Call this first whenever the user shares a YouTube link. If the user did not say what they want,
    call only this tool, then show a short menu in the user's language (tracks with the recommended one
    first, format, TXT layout, and action: download link, summary, translation, key points) and wait.
    `url` is a YouTube URL (watch, youtu.be, shorts, live, embed) or an 11-character video id.
    Returns title, channel, duration, published date (YYYY-MM-DD), original_language, `tracks`
    (manual uploader tracks first, then original-language auto captions such as "en-orig"; each
    with lang, name, auto, translated), `recommended` ({lang, auto} to pass to get_subtitles or
    get_download_link, or null), `options` (formats, layouts and actions for the menu) and `menu_hint`.
    Machine-translated auto captions are not listed because YouTube rate-limits them.
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
        options=OPTIONS,
        menu_hint=MENU_HINT,
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

    Call this only after the user picked what they want from the get_video_info menu, or when the
    request already makes it clear (for example "summarize this video": the recommended track as TXT
    paragraphs). To hand the user a file instead of reading the text, use get_download_link.
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


async def get_download_link(
    url: str,
    ctx: Context,
    lang: str | None = None,
    auto: bool | None = None,
    fmt: Literal["txt", "srt", "vtt"] = "txt",
    layout: Literal["paragraphs", "sentences", "cues"] = "paragraphs",
    include_header: bool = True,
) -> DownloadLinkOut:
    """Build a clickable link that downloads one subtitle track as a file from the web app.

    Use it when the user chose "download link" or asks for a file. It is fast: it resolves the track
    but does not download the subtitles. Show `download_url` to the user as a clickable link.
    `url`, `lang`, `auto`, `fmt`, `layout` (txt only) and `include_header` work as in get_subtitles
    (default: the recommended track, TXT, paragraphs, with header).
    Returns `download_url`, `web_app_url`, `filename`, `track` ({lang, name, auto}) and `note`.
    """
    info = await _load(url)
    track = _pick_track(info, (lang or "").strip() or None, auto)
    if fmt != "txt":
        layout = "paragraphs"  # layout only applies to TXT, as in /api/download
    query = urlencode({
        "url": info.video_id,
        "lang": track.lang,
        "auto": "true" if track.auto else "false",
        "fmt": fmt,
        "layout": layout,
        "header": "1" if include_header else "0",
    })
    base = _base_url(ctx)
    filename, _ = youtube.download_filename(info, track.lang, track.auto, fmt)
    return DownloadLinkOut(
        download_url=f"{base}/api/download?{query}",
        web_app_url=f"{base}/",
        filename=filename,
        track=DownloadTrackOut(lang=track.lang, name=track.name, auto=track.auto),
        note=READY_NOTE if base else UNCONFIGURED_NOTE,
    )


_register(get_video_info, structured=True)
_register(get_subtitles, structured=False)  # plain text; a structured copy would double the payload
_register(get_download_link, structured=True)
