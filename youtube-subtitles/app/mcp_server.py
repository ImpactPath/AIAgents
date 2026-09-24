"""MCP server (Model Context Protocol) exposing the subtitle downloader as three tools.

Mounted by app.main at /mcp (streamable HTTP with sessions, JSON responses), so
Claude and ChatGPT connectors can call it. The tools reuse the same cached
yt-dlp lookup, track rules, and converter as the REST API. Subtitle tracks are
also readable as resources (subtitles://...), and get_subtitles attaches the
track as an embedded resource plus a resource link so clients can show a file.
get_video_info is an MCP App (io.modelcontextprotocol/ui): hosts that render apps
show the interactive menu at ui://youtube-subtitles/menu.html (built from ui/
into app/ui/menu.html); other clients get the same menu as text. The menu lives on
get_video_info because clients that load tools lazily through a keyword search
find that tool first.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

import anyio
from mcp.server.apps import Apps, ResourceCsp, ResourcePermissions, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, EmbeddedResource, ResourceLink, TextContent, TextResourceContents
from pydantic import Field
from starlette.applications import Starlette
from typing_extensions import TypedDict  # pydantic needs this TypedDict on Python < 3.12

from app import __version__, youtube
from app.convert import convert, format_duration

log = logging.getLogger(__name__)

TWO_STEP_GUIDANCE = (
    "ask the user in two steps before fetching anything: step 1, what to do (download the subtitle file, "
    "summary, translation, key points); step 2, only if they chose download, which subtitle track (list the "
    "tracks by name and code, recommended first) and which format (TXT default, SRT, VTT). When the user's "
    "request is already explicit (for example 'summarize this video'), skip the questions and call "
    "get_subtitles with the recommended track as TXT paragraphs."
)
INSTRUCTIONS = (
    "Fetch YouTube subtitles. Whenever the user shares a YouTube link or video id, call get_video_info at once, "
    "before asking any question and before any other tool (never fetch the YouTube page with a web tool; that "
    "fails). get_video_info only looks up the title and the subtitle tracks and downloads nothing, so it is "
    "always safe to call first: it needs no permission, confirmation or user_confirmed flag, and asking the "
    "user whether to use the connector before calling it is wrong. Its result says what to do next: either the interactive subtitle menu is shown "
    "and you wait for the user's choice there, or, on clients without the menu, it lists the tracks and the "
    "two questions to ask. Never ask what to do with a video before get_video_info has returned. When the "
    "user's request is already explicit (for example 'summarize this video'), call get_video_info and then "
    "get_subtitles with the recommended track as TXT paragraphs, without questions. To give the user a file, "
    "call get_download_link and show its download_url as a clickable link; it is a public HTTPS address that "
    "works on any device. Machine-translated YouTube captions are rate-limited and hidden; download the "
    "original language and translate the text yourself if another language is needed. get_subtitles also "
    "attaches the file as a resource; tell the user it is attached if the client shows it. get_subtitles and "
    "get_download_link refuse to run until user_confirmed=true and get_video_info was called for that video "
    "in this session."
)
MENU_HINT = (
    "If the interactive subtitle menu is shown, the user picks there: wait for their choice. Otherwise, "
    + TWO_STEP_GUIDANCE
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
USER_CONFIRMED_DESCRIPTION = (
    "Set true only after the user explicitly chose what to do: they picked an action in the subtitle menu, "
    "answered your question, or their request itself was explicit (for example 'summarize this video'). "
    "When the user only shared a link, leave it false, call get_video_info, and ask."
)
NOT_CONFIRMED_ERROR = (
    "The user has not chosen yet. Call get_video_info first (it shows the subtitle menu), ask the user what "
    "they want (step 1: download file, summary, translation or key points; step 2, for downloads: which track "
    "and which format), then call this tool again with user_confirmed=true."
)
NOT_LOOKED_UP_ERROR = "Call get_video_info for this video first so the user can see the tracks and choose."
UserConfirmed = Annotated[bool, Field(description=USER_CONFIRMED_DESCRIPTION)]
MAX_GATED_SESSIONS = 1024  # sessions remembered by the get_video_info gate (least recently used dropped)
MAX_GATED_VIDEOS = 64  # video ids remembered per session
MIME_TYPES = {"txt": "text/plain", "srt": "application/x-subrip", "vtt": "text/vtt"}
URI_SCHEME = "subtitles://"
TRACK_URI_PREFIX = URI_SCHEME + "video/"  # constant host: clients may lowercase a URI host, ids are case-sensitive

MENU_URI = "ui://youtube-subtitles/menu.html"
MENU_HTML_PATH = Path(__file__).resolve().parent / "ui" / "menu.html"
MENU_PLACEHOLDER_HTML = (
    "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Subtitle menu</title></head>\n"
    "<body><p>The subtitle menu view is not built yet (run npm run build in ui/).</p>\n"
    "<script>window.parent.postMessage({jsonrpc: \"2.0\", id: 1, method: \"ui/initialize\", params: {"
    "protocolVersion: \"2026-01-26\", appInfo: {name: \"YouTube Subtitles\", version: \"0\"}, "
    "appCapabilities: {}}}, \"*\");</script>\n</body></html>\n"
)
UNDECLARED_APPS_NOTE = (
    "This client did not declare whether it renders MCP Apps. If the interactive subtitle menu is shown, the "
    "user picks there: keep your reply short and wait. Otherwise ask the user as described at the end."
)
MENU_FORMATS = [{"id": "txt", "label": "TXT"}, {"id": "srt", "label": "SRT"}, {"id": "vtt", "label": "VTT"}]
MENU_LAYOUTS = [
    {"id": "paragraphs", "label": "Paragraphs"},
    {"id": "sentences", "label": "Sentences"},
    {"id": "cues", "label": "Original cues"},
]
MENU_DEFAULTS = {"fmt": "txt", "layout": "paragraphs"}
MENU_LABELS = {
    "en": {
        "subtitles": "Subtitles", "format": "Format", "layout": "Text layout",
        "paragraphs": "Paragraphs", "sentences": "Sentences", "cues": "Original cues",
        "original": "original", "auto": "auto", "download": "Download", "preview": "Preview",
        "summarize": "Summarize", "translate": "Translate to Korean", "keypoints": "Key points",
        "copy": "Copy", "copied": "Copied", "openWeb": "Open web app", "selected": "Selected",
        "downloading": "Preparing file...", "downloadReady": "If the download did not start, open this link:",
        "loading": "Loading...", "error": "Something went wrong",
        "report": "Summary report (.md)", "addToChat": "Add to chat",
        "addedToChat": "Subtitles placed in the message box. Press send to add them to the chat.",
        "expand": "Expand", "collapse": "Back to chat size",
    },
    "ko": {
        "subtitles": "자막", "format": "파일 형식", "layout": "텍스트 줄 정돈",
        "paragraphs": "문단", "sentences": "한 문장씩", "cues": "원본 줄",
        "original": "원본", "auto": "자동", "download": "다운로드", "preview": "미리 보기",
        "summarize": "요약", "translate": "한국어로 번역", "keypoints": "핵심 정리",
        "copy": "복사", "copied": "복사됨", "openWeb": "웹앱 열기", "selected": "선택됨",
        "downloading": "파일 준비 중...",
        "downloadReady": "다운로드가 시작되지 않으면 이 링크를 여세요:",
        "loading": "불러오는 중...", "error": "오류가 발생했습니다",
        "report": "요약 보고서 (.md)", "addToChat": "채팅에 넣기",
        "addedToChat": "자막을 입력창에 넣었습니다. 전송을 누르면 채팅에 추가됩니다.",
        "expand": "크게 보기", "collapse": "채팅 크기로",
    },
}


def _load_menu_html() -> str:
    """The built view (app/ui/menu.html); a placeholder with a warning when it has not been built yet."""
    try:
        return MENU_HTML_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("%s is missing; serving a placeholder for %s. Build it with: cd ui && npm install && "
                    "npm run build", MENU_HTML_PATH, MENU_URI)
        return MENU_PLACEHOLDER_HTML


MENU_HTML = _load_menu_html()
apps = Apps()


class TrackOut(TypedDict):
    lang: str
    name: str
    auto: bool
    translated: bool
    kind: Literal["original", "auto"]


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


class MenuVideoOut(TypedDict):
    video_id: str
    title: str
    channel: str | None
    duration: str | None
    duration_seconds: int | None
    published: str | None
    url: str
    thumbnail: str | None
    original_language: str | None


class VideoInfoOut(TypedDict):
    """get_video_info's structuredContent: the fields for the model plus the view contract (video ... labels)."""

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
    video: MenuVideoOut
    formats: list[OptionOut]
    layouts: list[OptionOut]
    defaults: dict[str, str]
    base_url: str
    download_template: str
    labels: dict[str, dict[str, str]]


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
    startup. Sessions are stateful (Mcp-Session-Id) so the capabilities a client
    declares in initialize, such as MCP Apps support, are known on every later
    request; sessions live in this process, so run a single uvicorn worker.
    Idle sessions expire after the SDK default (30 minutes). Host/Origin checks
    are off: the app runs behind proxies (Hugging Face, Render) with arbitrary
    Host headers, and /mcp has its own API key guard.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=False,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


# Per MCP session: the video ids get_video_info was called for, so get_subtitles and get_download_link can
# refuse a video the user has not seen the menu for. Keyed by the Mcp-Session-Id the session manager already
# validated (an unknown id is answered 404 before any tool runs); entries are dropped when the session's
# connection closes, and the dict is capped as a fallback.
_looked_up: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()
LOCAL_SESSION = "local"  # stdio (one client per process), a request without a session id, or a direct call


def _session_key(ctx: Context) -> str:
    """A stable id for the MCP session carrying this request.

    The SDK builds a new ServerSession per request, so its identity is not stable; the Mcp-Session-Id
    header (the same value as the connection's session_id) is.
    """
    try:
        request_context = ctx.request_context
    except (ValueError, RuntimeError):  # no active request (a direct call)
        return LOCAL_SESSION
    headers = getattr(request_context.request, "headers", None)
    session_id = headers.get("mcp-session-id") if headers else None
    return f"http:{session_id}" if session_id else LOCAL_SESSION


def _forget_session(key: str) -> None:
    _looked_up.pop(key, None)


def _remember_video(ctx: Context, video_id: str) -> None:
    key = _session_key(ctx)
    videos = _looked_up.get(key)
    if videos is None:
        videos = _looked_up[key] = OrderedDict()
        # Clear the entry when the connection closes (its exit stack unwinds on DELETE, idle timeout or crash).
        connection = getattr(getattr(ctx, "session", None), "_connection", None) if key != LOCAL_SESSION else None
        exit_stack = getattr(connection, "exit_stack", None)
        if exit_stack is not None:
            exit_stack.callback(_forget_session, key)
    _looked_up.move_to_end(key)
    videos[video_id] = None
    videos.move_to_end(video_id)
    while len(videos) > MAX_GATED_VIDEOS:
        videos.popitem(last=False)
    while len(_looked_up) > MAX_GATED_SESSIONS:
        _looked_up.popitem(last=False)


def _require_choice(url: str, ctx: Context, user_confirmed: bool) -> None:
    """Refuse unless the user chose (user_confirmed) and get_video_info ran for this video in this session."""
    if not user_confirmed:
        raise ToolError(NOT_CONFIRMED_ERROR)
    video_id = youtube.parse_video_id(url)
    if not video_id:
        raise ToolError(youtube.INVALID_URL)
    videos = _looked_up.get(_session_key(ctx))
    if videos is None or video_id not in videos:
        raise ToolError(NOT_LOOKED_UP_ERROR)


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


async def get_subtitles(
    url: str,
    ctx: Context,
    user_confirmed: UserConfirmed = False,
    lang: str | None = None,
    auto: bool | None = None,
    fmt: Literal["txt", "srt", "vtt"] = "txt",
    layout: Literal["paragraphs", "sentences", "cues"] = "paragraphs",
    include_header: bool = True,
    max_chars: int = 200000,
    attach: bool = True,
) -> list[TextContent | EmbeddedResource | ResourceLink]:
    """Requires user_confirmed=true; see that parameter. Download the subtitles of a YouTube video as text.

    Call this only after the user picked what they want from the subtitle menu, or when the
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
    _require_choice(url, ctx, user_confirmed)
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
    user_confirmed: UserConfirmed = False,
    lang: str | None = None,
    auto: bool | None = None,
    fmt: Literal["txt", "srt", "vtt"] = "txt",
    layout: Literal["paragraphs", "sentences", "cues"] = "paragraphs",
    include_header: bool = True,
) -> DownloadLinkOut:
    """Requires user_confirmed=true; see that parameter. Build a clickable link that downloads one subtitle
    track as a file from the web app.

    Use it when the user chose "download link" or asks for a file. It is fast: it resolves the track
    but does not download the subtitles. Show `download_url` to the user as a clickable link.
    `url`, `lang`, `auto`, `fmt`, `layout` (txt only) and `include_header` work as in get_subtitles
    (default: the recommended track, TXT, paragraphs, with header).
    Returns `download_url`, `web_app_url`, `filename`, `track` ({lang, name, auto}) and `note`.
    """
    _require_choice(url, ctx, user_confirmed)
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


def _text_menu(data: VideoInfoOut) -> str:
    """The subtitle menu as plain text, for clients that cannot render the interactive view."""
    video = data["video"]
    facts = ", ".join(x for x in (video["channel"], video["duration"], video["published"]) if x)
    rec = data["recommended"]
    lines = [f"Subtitle menu for '{video['title']}'" + (f" ({facts})" if facts else "") + f": {video['url']}", "",
             "Tracks (pass lang and auto to get_subtitles or get_download_link):"]
    for i, t in enumerate(data["tracks"], 1):
        mark = " (recommended)" if rec and (t["lang"], t["auto"]) == (rec["lang"], rec["auto"]) else ""
        lines.append(f"{i}. {t['name']}, {t['kind']}: lang={t['lang']}, auto={str(t['auto']).lower()}{mark}")
    lines += [
        "",
        "Formats (fmt): " + ", ".join(f"{f['label']} ({f['id']})" for f in data["formats"]) + "; default txt.",
        "Text layout for TXT (layout): " + ", ".join(f"{x['label']} ({x['id']})" for x in data["layouts"])
        + "; default paragraphs.",
        "Actions: download the subtitle file (get_download_link), preview the text (get_subtitles), summarize, "
        "translate, key points.",
        "",
        TWO_STEP_GUIDANCE[0].upper() + TWO_STEP_GUIDANCE[1:],
    ]
    return "\n".join(lines)


def _apps_support(ctx: Context) -> bool | None:
    """True or False as the client declared it, None when it declared no capabilities (or no request)."""
    try:
        if ctx.client_capabilities is None:
            return None
        return client_supports_apps(ctx)
    except (ValueError, RuntimeError):  # no active request (a direct call)
        return None


def _video_info_data(info: youtube.VideoInfo, base: str) -> VideoInfoOut:
    tracks = youtube.downloadable_tracks(info)
    rec = youtube.recommended_track(info)
    duration = format_duration(info.duration) or None
    return VideoInfoOut(
        video_id=info.video_id,
        title=info.title,
        channel=info.channel,
        duration_seconds=info.duration,
        duration=duration,
        published=info.upload_date,
        url=info.webpage_url,
        original_language=info.original_language,
        tracks=[TrackOut(**t.public(), kind="auto" if t.auto else "original") for t in tracks],
        recommended=RecommendedOut(lang=rec.lang, auto=rec.auto) if rec else None,
        options=OPTIONS,
        menu_hint=MENU_HINT,
        video=MenuVideoOut(
            video_id=info.video_id,
            title=info.title,
            channel=info.channel,
            duration=duration,
            duration_seconds=info.duration,
            published=info.upload_date,
            url=info.webpage_url,
            thumbnail=info.thumbnail or f"https://i.ytimg.com/vi/{info.video_id}/hqdefault.jpg",
            original_language=info.original_language,
        ),
        formats=[OptionOut(**f) for f in MENU_FORMATS],
        layouts=[OptionOut(**x) for x in MENU_LAYOUTS],
        defaults=dict(MENU_DEFAULTS),
        base_url=base,
        download_template=(f"{base}/api/download?url={info.video_id}&lang={{lang}}&auto={{auto}}"
                           "&fmt={fmt}&layout={layout}&header=1"),
        labels=MENU_LABELS,
    )


@apps.tool(
    resource_uri=MENU_URI,
    name="get_video_info",
    title="YouTube video info and subtitle menu",
    description=(
        "Look up a YouTube video link: title, channel, duration, and the downloadable subtitle tracks (uploader "
        "subtitles and original-language auto captions), and show the interactive subtitle menu (download the "
        "subtitle file, preview, summarize, translate, key points). Call this at once, before asking the user "
        "anything, whenever they share a YouTube URL or video id; it downloads no subtitles and needs no "
        "confirmation or user_confirmed flag. Returns tracks "
        "with lang/auto to pass to get_subtitles or get_download_link, a recommended track, and the next step."
    ),
)
async def get_video_info(url: str, ctx: Context) -> Annotated[CallToolResult, VideoInfoOut]:
    """Video facts and tracks for the model plus the menu data for the view, in one structuredContent.

    `url` is a YouTube URL (watch, youtu.be, shorts, live, embed) or an 11-character video id. `tracks`
    lists manual uploader tracks first, then original-language auto captions such as "en-orig";
    machine-translated auto captions are left out because YouTube rate-limits them.
    The text content is a "wait for the user" status when the client declared MCP Apps support, and the
    whole menu as text (ending with the two-step questions) when it declared no support. A request that
    carries no client capabilities (for example a 2026-07-28 request without them, or a direct call) gets
    the text menu with UNDECLARED_APPS_NOTE, since the host may still render the view.
    """
    info = await _load(url)
    _remember_video(ctx, info.video_id)
    data = _video_info_data(info, _base_url(ctx))
    apps_support = _apps_support(ctx)
    if apps_support:
        count = len(data["tracks"])
        langs = ", ".join(t["lang"] + (" (auto)" if t["auto"] else "") for t in data["tracks"])
        text = (
            f"Interactive menu shown for '{info.title}' ({count} {'track' if count == 1 else 'tracks'}: {langs}). "
            "The user is choosing a track, format and action in the menu. Wait for their choice; do not call "
            "get_subtitles or get_download_link until the user picks an action or asks explicitly."
        )
    else:
        text = _text_menu(data)
        if apps_support is None:
            text = UNDECLARED_APPS_NOTE + "\n\n" + text
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


apps.add_html_resource(
    MENU_URI,
    MENU_HTML,
    name="Subtitle menu",
    description="Interactive subtitle menu for one YouTube video (MCP App view).",
    csp=ResourceCsp(resource_domains=["https://i.ytimg.com", "https://*.ytimg.com"]),
    permissions=ResourcePermissions(clipboard_write={}),
    prefers_border=True,
)
server = MCPServer(
    name="youtube-subtitles",
    title="YouTube Subtitle Downloader",
    version=__version__,
    instructions=INSTRUCTIONS,
    extensions=[apps],
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
