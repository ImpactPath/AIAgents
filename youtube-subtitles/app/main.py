"""FastAPI app: serves the single-page UI and the subtitle API."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import youtube
from app.convert import FORMATS, LAYOUTS, convert

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MEDIA_TYPES = {
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}
BOOL_VALUES = {"true": True, "1": True, "false": False, "0": False}
INVALID_URL = "Please enter a valid YouTube video URL or 11-character video ID."

app = FastAPI(title="YouTube Subtitle Downloader")
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
        raise HTTPException(400, INVALID_URL)
    return video_id


async def _load_info(video_id: str) -> youtube.VideoInfo:
    try:
        return await run_in_threadpool(youtube.fetch_info, video_id)
    except youtube.YoutubeError as exc:
        raise HTTPException(502, f"Could not fetch video info: {exc}") from exc


def safe_title(title: str, fallback: str) -> str:
    """Make a title safe for a filename: no reserved chars, collapsed spaces, max 80 chars."""
    name = "".join(ch for ch in title if unicodedata.category(ch)[0] != "C")
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:80].strip(" .")
    return name or fallback


def content_disposition(filename: str, ascii_fallback: str) -> str:
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/info")
async def api_info(url: str = "") -> dict:
    info = await _load_info(_video_id(url))
    if not info.tracks:
        raise HTTPException(404, "This video has no subtitles or auto-generated captions.")
    return info.public()


@app.get("/api/download")
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
    meta = None
    if header_flag:
        meta = {
            "title": info.title,
            "channel": info.channel,
            "duration": info.duration,
            "upload_date": info.upload_date,
            "url": info.webpage_url,
            "track_name": track.name,
            "track_lang": track.lang,
            "track_auto": track.auto,
        }
    body = convert(raw, fmt, layout, header=meta)
    if not body.strip() or body.strip() == "WEBVTT":
        raise HTTPException(502, "The subtitle track was empty or could not be parsed.")

    title = safe_title(info.title, video_id)
    lang_part = re.sub(r"[^A-Za-z0-9_-]", "", lang) or "sub"
    suffix = f".{lang_part}{'.auto' if auto_flag else ''}.{fmt}"
    ascii_title = title if title.isascii() else video_id
    return Response(
        content=body.encode("utf-8"),
        media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": content_disposition(title + suffix, ascii_title + suffix)},
    )


@app.get("/", include_in_schema=False)
async def index() -> Response:
    page = STATIC_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(404, "static/index.html not found.")
    return FileResponse(page, media_type="text/html; charset=utf-8")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
