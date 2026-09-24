"""FastAPI app: serves the single-page UI and the subtitle API."""

from __future__ import annotations

import hmac
import logging
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app import __version__, mcp_server, youtube
from app.convert import FORMATS, LAYOUTS, convert
from app.youtube import safe_title  # noqa: F401  (re-exported for tests)

log = logging.getLogger(__name__)
if not logging.getLogger().handlers:  # uvicorn only configures its own loggers
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MEDIA_TYPES = {
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}
BOOL_VALUES = {"true": True, "1": True, "false": False, "0": False}



@asynccontextmanager
async def lifespan(_: FastAPI):
    if not os.environ.get("MCP_API_KEY", "").strip():
        log.warning("MCP_API_KEY is not set: the MCP endpoint at /mcp is open to anyone.")
    mcp_server.build_http_app()  # a fresh session manager per lifespan (each can run only once)
    async with mcp_server.server.session_manager.run():
        yield


app = FastAPI(
    title="YouTube Subtitle Downloader",
    version=__version__,
    description="List and download YouTube subtitles (SRT, VTT, plain text). Also serves an MCP endpoint at /mcp.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    fields = sorted({str(e.get("loc", ["", "?"])[-1]) for e in exc.errors()})
    return JSONResponse({"detail": f"Missing or invalid parameter: {', '.join(fields)}."}, status_code=400)


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Internal server error."}, status_code=500)


def _video_id(url: str) -> str:
    video_id = youtube.parse_video_id(url)
    if not video_id:
        raise HTTPException(400, youtube.INVALID_URL)
    return video_id


async def _load_info(video_id: str) -> youtube.VideoInfo:
    try:
        return await run_in_threadpool(youtube.fetch_info, video_id)
    except youtube.YoutubeError as exc:
        raise HTTPException(502, f"Could not fetch video info: {exc}") from exc


def content_disposition(filename: str, ascii_fallback: str) -> str:
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    pot = await run_in_threadpool(youtube.pot_provider_status)
    return {"status": "ok", "pot_provider": pot}


@app.get(
    "/api/info",
    operation_id="get_video_info",
    summary="Get video info and subtitle tracks",
    description=(
        "Look up a YouTube video by URL or 11-character id. Returns title, channel, duration, upload_date, "
        "original_language, and every subtitle track (lang, name, auto, translated). Tracks with "
        "translated=true are machine-translated by YouTube and often rate-limited (HTTP 429); prefer the "
        "original-language track."
    ),
)
async def api_info(url: str = "") -> dict:
    info = await _load_info(_video_id(url))
    if not info.tracks:
        raise HTTPException(404, youtube.no_subtitles_message(info))
    return info.public()


@app.get(
    "/api/download",
    operation_id="download_subtitles",
    summary="Download subtitles",
    description=(
        "Download one subtitle track as a file. lang and auto select the track (see get_video_info). "
        "fmt is srt, vtt or txt (default srt); layout (txt only) is paragraphs (default), sentences or cues; "
        "header=1 (default) starts the file with the video title, channel, duration, date, URL and track."
    ),
)
async def api_download(
    url: str = "",
    lang: str = "",
    auto: str = "false",
    fmt: str = "srt",
    layout: str = LAYOUTS[0],
    header: str = "1",
) -> Response:
    video_id = _video_id(url)
    fmt = fmt.strip().lower()
    if fmt not in FORMATS:
        raise HTTPException(400, f"Unsupported format '{fmt}'. Use one of: {', '.join(FORMATS)}.")
    layout = layout.strip().lower()
    if fmt != "txt":
        layout = LAYOUTS[0]  # layout only applies to TXT; ignore whatever was sent
    elif layout not in LAYOUTS:
        raise HTTPException(400, f"Unsupported layout '{layout}'. Use one of: {', '.join(LAYOUTS)}.")
    auto_flag = BOOL_VALUES.get(auto.strip().lower())
    if auto_flag is None:
        raise HTTPException(400, "Parameter 'auto' must be true, false, 1, or 0.")
    header_flag = BOOL_VALUES.get(header.strip().lower())
    if header_flag is None:
        raise HTTPException(400, "Parameter 'header' must be true, false, 1, or 0.")
    lang = lang.strip()
    if not lang:
        raise HTTPException(400, "Parameter 'lang' is required.")

    info = await _load_info(video_id)
    track = info.find_track(lang, auto_flag)
    if not track:
        kind = "auto-generated" if auto_flag else "manual"
        raise HTTPException(404, f"No {kind} subtitle track for language '{lang}'.")
    try:
        raw = await run_in_threadpool(youtube.fetch_subtitle_text, track)
    except youtube.YoutubeError as exc:
        raise HTTPException(502, f"Could not download subtitles: {exc}") from exc
    meta = info.header_meta(track) if header_flag else None
    body = convert(raw, fmt, layout, header=meta)
    if not body.strip() or body.strip() == "WEBVTT":
        raise HTTPException(502, "The subtitle track was empty or could not be parsed.")

    filename, ascii_name = youtube.download_filename(info, lang, auto_flag, fmt)
    return Response(
        content=body.encode("utf-8"),
        media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": content_disposition(filename, ascii_name)},
    )


def _mcp_key_ok(scope: Scope, expected: str) -> bool:
    """Accept the key as "Authorization: Bearer <key>", "X-API-Key: <key>", or "?key=<key>"."""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    candidates = [headers.get("x-api-key", "")]
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        candidates.append(token.strip())
    candidates += parse_qs(scope.get("query_string", b"").decode("latin-1")).get("key", [])
    want = expected.encode("utf-8")
    return any(c and hmac.compare_digest(c.strip().encode("utf-8"), want) for c in candidates)


class McpApiKeyGuard:
    """Pure ASGI guard for the MCP endpoint only: requires MCP_API_KEY when it is set."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        expected = os.environ.get("MCP_API_KEY", "").strip()
        if scope["type"] == "http" and expected and not _mcp_key_ok(scope, expected):
            response = JSONResponse({"detail": "Missing or invalid MCP API key."}, status_code=401)
            await response(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("method") == "POST":
            receive = _log_mcp_request(receive)
        await self.app(scope, receive, send)


def _log_mcp_request(receive: Receive) -> Receive:
    """Log which MCP client connects (initialize) and which tools it calls; the body is passed through."""
    chunks: list[bytes] = []

    async def wrapped() -> dict:
        message = await receive()
        if message.get("type") == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                _describe_mcp_body(b"".join(chunks))
        return message

    return wrapped


def _describe_mcp_body(body: bytes) -> None:
    if not body or len(body) > 1_000_000:
        return
    try:
        msg = json.loads(body)
    except ValueError:
        return
    for item in msg if isinstance(msg, list) else [msg]:
        if not isinstance(item, dict):
            continue
        method, params = item.get("method"), item.get("params") or {}
        if method == "initialize":
            info = params.get("clientInfo") or {}
            caps = params.get("capabilities") or {}
            ext = caps.get("extensions") or {}
            apps = ext.get("io.modelcontextprotocol/ui")
            log.info(
                "MCP client connected: %s %s, protocol %s, extensions %s, apps=%s",
                info.get("name"), info.get("version"), params.get("protocolVersion"),
                sorted(ext) if isinstance(ext, dict) else ext, apps,
            )
        elif method == "tools/call":
            log.info("MCP tools/call %s", params.get("name"))


async def _mcp_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    try:
        manager = mcp_server.server.session_manager
    except RuntimeError:
        await JSONResponse({"detail": "MCP endpoint is not ready."}, status_code=503)(scope, receive, send)
        return
    await manager.handle_request(scope, receive, send)


# Plain routes (not a Mount) so both /mcp and /mcp/ work without a redirect.
for _path in ("/mcp", "/mcp/"):
    app.add_route(_path, McpApiKeyGuard(_mcp_endpoint), include_in_schema=False)


def _openapi() -> dict:
    """OpenAPI schema; PUBLIC_BASE_URL (read per call) becomes the server URL for GPT Actions."""
    base = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    app.servers = [{"url": base}] if base else []
    app.openapi_schema = None
    return FastAPI.openapi(app)


app.openapi = _openapi


@app.get("/", include_in_schema=False)
async def index() -> Response:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(404, "static/index.html not found.")
    return FileResponse(page, media_type="text/html; charset=utf-8")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
