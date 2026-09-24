"""MCP server (Model Context Protocol) exposing the subtitle downloader as three tools.

Mounted by app.main at /mcp (streamable HTTP, stateless, JSON responses), so
Claude and ChatGPT connectors can call it. The tools reuse the same cached
yt-dlp lookup, track rules, and converter as the REST API. Subtitle tracks are
also readable as resources (subtitles://...), and get_subtitles attaches the
track as an embedded resource plus a resource link so clients can show a file.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Literal
from urllib.parse import quote, urlencode

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import EmbeddedResource, ResourceLink, TextContent, TextResourceContents
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
    "language is needed. get_subtitles also attaches the file as a resource; tell the user it is attached "
    "if the client shows it."
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
READY_NOTE = (
    "Open the link in any browser on any device to save the file. It is a public HTTPS address: no login, "
    "no Tailscale or VPN needed. Tell the user exactly that."
)
MIME_TYPES = {"txt": "text/plain", "srt": "application/x-subrip", "vtt": "text/vtt"}
URI_SCHEME = "subtitles://"
TRACK_URI_PREFIX = URI_SCHEME + "video/"  # constant host: clients may lowercase a URI host, ids are case-sensitive

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


async def _render(info: youtube.VideoInfo, track: youtube.Track, fmt: str, layout: str, include_header: bool) -> str:
    """Download one track and convert it, as get_subtitles and the subtitles:// resources return it."""
    try:
        raw = await anyio.to_thread.run_sync(youtube.fetch_subtitle_text, track)
    except youtube.YoutubeError as exc:
        raise ToolError(f"Could not download subtitles: {exc}") from exc
    header = info.header_meta(track) if include_header else None
    body = convert(raw, fmt, layout, header=header)  # layout is ignored for srt/vtt
    if not body.strip() or body.strip() == "WEBVTT":
        raise ToolError("The subtitle track was empty or could not be parsed.")
    return body


def subtitles_uri(video_id: str, lang: str, auto: bool, fmt: str, layout: str, include_header: bool = True) -> str:
    """Resource URI of one rendered track, e.g. subtitles://video/dQw4w9WgXcQ/en/false/txt/paragraphs.

    The layout segment only matters for txt; srt and vtt always use "cues". "?header=0" drops the header.
    """
    layout = layout if fmt == "txt" else "cues"
    uri = f"{TRACK_URI_PREFIX}{video_id}/{quote(lang, safe='')}/{'true' if auto else 'false'}/{fmt}/{layout}"
    return uri if include_header else uri + "?header=0"


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
    attach: bool = True,
) -> list[TextContent | EmbeddedResource | ResourceLink]:
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
    `attach` (default true): after the text, also return the same text as an embedded resource and a
    resource link (subtitles://video/<video_id>/<lang>/<auto>/<fmt>/<layout>) named like the download file,
    so the client can show it as an attached file; false returns the text only.
    get_subtitles also attaches the file as a resource; tell the user it is attached if the client shows it.
    Machine-translated auto captions (another language than the video's) are rate-limited by YouTube
    (HTTP 429); fetch the original language instead and translate the text yourself.
    """
    if max_chars < 1:
        raise ToolError("max_chars must be a positive number.")
    info = await _load(url)
    track = _pick_track(info, (lang or "").strip() or None, auto)
    text = _truncate(await _render(info, track, fmt, layout, include_header), max_chars)
    content: list[TextContent | EmbeddedResource | ResourceLink] = [TextContent(type="text", text=text)]
    if not attach:
        return content
    uri = subtitles_uri(info.video_id, track.lang, track.auto, fmt, layout, include_header)
    mime_type = MIME_TYPES[fmt]
    filename, _ = youtube.download_filename(info, track.lang, track.auto, fmt)
    content.append(EmbeddedResource(
        type="resource", resource=TextResourceContents(uri=uri, mime_type=mime_type, text=text),
    ))
    content.append(ResourceLink(
        type="resource_link", name=filename, title=f"{info.title} ({track.name}, {fmt.upper()})", uri=uri,
        description="Subtitle file", mime_type=mime_type, size=len(text.encode("utf-8")),
    ))
    return content


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


def _flag(value: str, name: str) -> bool:
    flag = {"true": True, "1": True, "auto": True, "false": False, "0": False, "manual": False}.get(value.lower())
    if flag is None:
        raise ResourceError(f"'{name}' must be true or false, not '{value}'.")
    return flag


async def _read_track(fmt: str, video_id: str, lang: str, auto: str, layout: str, header: str) -> str:
    """Resolve and render one track for a subtitles:// resource read (exact lang and auto match)."""
    auto_flag, header_flag = _flag(auto, "auto"), _flag(header, "header")
    if fmt == "txt" and layout not in ("paragraphs", "sentences", "cues"):
        raise ResourceError(f"Unknown layout '{layout}'; use paragraphs, sentences or cues.")
    try:
        info = await _load(video_id)
        track = info.find_track(lang, auto_flag)
        if not track:
            kind = "auto-generated" if auto_flag else "manual"
            raise ToolError(f"No {kind} subtitle track for language '{lang}'.")
        return await _render(info, track, fmt, layout if fmt == "txt" else "paragraphs", header_flag)
    except ToolError as exc:
        raise ResourceError(str(exc)) from exc


def _register_track_template(fmt: str) -> None:
    async def read_subtitles(video_id: str, lang: str, auto: str, layout: str, header: str = "1") -> str:
        return await _read_track(fmt, video_id, lang, auto, layout, header)

    server.resource(
        f"{TRACK_URI_PREFIX}{{video_id}}/{{lang}}/{{auto}}/{fmt}/{{layout}}{{?header}}",
        name=f"subtitles_{fmt}",
        title=f"YouTube subtitles ({fmt.upper()})",
        description=(
            f"One subtitle track as {fmt.upper()}, with the metadata header unless ?header=0. `auto` is true "
            "for auto-generated captions, false for uploader subtitles. `layout` is paragraphs, sentences or "
            "cues for txt; srt and vtt ignore it (get_subtitles uses cues)."
        ),
        mime_type=MIME_TYPES[fmt],
    )(read_subtitles)


def recent_videos() -> str:
    """Videos looked up in the last 10 minutes, newest first, with the resource URI of the recommended track."""
    videos = []
    for info in youtube.cached_infos():
        rec = youtube.recommended_track(info)
        videos.append({
            "video_id": info.video_id,
            "title": info.title,
            "channel": info.channel,
            "url": info.webpage_url,
            "subtitles_uri": subtitles_uri(info.video_id, rec.lang, rec.auto, "txt", "paragraphs") if rec else None,
        })
    return json.dumps({"videos": videos}, ensure_ascii=False, indent=2)


_register(get_video_info, structured=True)
_register(get_subtitles, structured=False)  # content blocks; a structured copy would duplicate the payload again
_register(get_download_link, structured=True)
for _fmt in MIME_TYPES:
    _register_track_template(_fmt)
server.resource(
    f"{URI_SCHEME}recent",
    name="recent_videos",
    title="Recently looked-up videos",
    description=inspect.cleandoc(recent_videos.__doc__ or ""),
    mime_type="application/json",
)(recent_videos)
