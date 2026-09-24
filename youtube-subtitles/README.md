# YouTube Subtitle Downloader

A small self-hosted web app that lists the subtitle tracks of a YouTube video and downloads them as
SRT, VTT, or plain text. The backend is FastAPI plus [yt-dlp](https://github.com/yt-dlp/yt-dlp); the
frontend is a single static HTML page (`static/index.html`) with no build step.

Paste a video URL, pick a manual or auto-generated track, choose a format, and download or preview it.
Nothing is stored on disk: video info is kept in memory for 10 minutes so the lookup and the download
share one yt-dlp extraction.

## Layout

```
app/main.py       FastAPI app: API endpoints, serves static/
app/youtube.py    URL parsing, yt-dlp wrapper, TTL cache
app/convert.py    pure VTT/SRT parsing and SRT/VTT/TXT rendering
static/index.html single-file UI
tests/            pytest suite (yt-dlp is mocked, no network needed)
```

## Run locally

Requires Python 3.11+.

```bash
cd youtube-subtitles
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 7860
```

Open http://localhost:7860. Run the tests with `python -m pytest -q`.

## Run with Docker

```bash
cd youtube-subtitles
docker build -t yt-subs .
docker run --rm -p 7860:7860 yt-subs
```

The image runs as a non-root user (uid 1000) and listens on `$PORT`, defaulting to 7860.

## API

| Endpoint | Description |
| --- | --- |
| `GET /healthz` | `{"status": "ok"}` |
| `GET /api/info?url=<url or id>` | Title, channel, duration, thumbnail, `upload_date` (ISO `YYYY-MM-DD` or null), `url` (canonical watch URL) and `tracks: [{lang, name, auto}]` (manual tracks first) |
| `GET /api/download?url=&lang=en&auto=false&fmt=srt&layout=paragraphs&header=1` | The subtitle file as an attachment. `fmt` is `srt`, `vtt` or `txt`; `auto` is `true/false/1/0`; `layout` (TXT only, ignored for SRT/VTT) is `paragraphs` (default: sentences grouped into paragraphs, split at pauses of 2 s or more or after about 600 characters), `sentences` (one sentence per line) or `cues` (one caption cue per line, the raw timing breaks); `header` (`true/false/1/0`, default on) starts the file with the title, channel, duration, publish date, URL and track (a `NOTE` block in VTT, a 0 to 1 ms first cue in SRT, plain lines in TXT) |

Track names are normalized: YouTube's "(Original)" and "(auto-generated)" are removed (the `auto` flag
says whether a track is auto-generated), and the unedited speech-recognition track (`<lang>-orig`, for
example `en-orig`) is named "English (unedited)". In file headers, manual tracks are tagged `[original]`
(uploader-provided) and auto tracks `[auto-generated]`.

Errors are JSON `{"detail": "..."}`: 400 for an invalid URL or parameter, 404 when the video (or the
requested track) has no subtitles, 502 when yt-dlp fails (private, removed, geo-blocked, bot check,
network).

Accepted inputs: `youtube.com/watch?v=`, `youtu.be/<id>`, `/shorts/<id>`, `/live/<id>`, `/embed/<id>`,
`m.youtube.com`, `music.youtube.com`, or a bare 11-character video id. The server only ever passes a
rebuilt `https://www.youtube.com/watch?v=<id>` URL to yt-dlp, never the raw user input.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `YTDLP_COOKIES` | Path to a Netscape-format `cookies.txt`, passed to yt-dlp as `cookiefile`. Helps with "Sign in to confirm you're not a bot". |
| `YTDLP_PROXY` | Proxy URL for yt-dlp, for example `http://user:pass@host:port` or `socks5://host:1080`. |
| `PORT` | Listening port inside the Docker image (default 7860). |

## Free deployment

### Hugging Face Spaces (Docker)

1. Create a new Space and choose the **Docker** SDK (blank template).
2. Push the contents of this `youtube-subtitles/` folder to the Space repository root.
3. Make sure the Space `README.md` starts with this front matter (Spaces reads its config from it):

   ```yaml
   ---
   title: YouTube Subtitle Downloader
   sdk: docker
   app_port: 7860
   ---
   ```

4. Optional: add `YTDLP_COOKIES` / `YTDLP_PROXY` under Settings, Variables and secrets. To use a cookies
   file, store its contents as a secret and write it to a file at startup, or bake it into a private Space.

Spaces expects port 7860 and uid 1000, which the Dockerfile already uses.

### Render

1. New **Web Service**, connect the GitHub repository, set **Root Directory** to `youtube-subtitles`.
2. Runtime: **Docker** (Render builds the Dockerfile and sets `PORT`, which the image honors).
3. Health check path: `/healthz`. Add the environment variables above if needed.

The free Render tier sleeps when idle, so the first request after a pause is slow.

## Caveats

- YouTube often blocks requests from cloud and datacenter IPs with a "Sign in to confirm you're not a
  bot" check. When that happens the API returns 502 with that message. A cookies file (`YTDLP_COOKIES`)
  from a logged-in browser session, or a residential proxy (`YTDLP_PROXY`), usually fixes it. Use a
  throwaway account for cookies.
- yt-dlp must stay current as YouTube changes. Rebuild the image (or `pip install -U "yt-dlp[default,deno]"`)
  when extraction starts failing. The `deno` extra provides the JavaScript runtime that recent yt-dlp
  versions use for YouTube.
- Auto-generated captions use "rolling" cues on YouTube. The converter removes the repeated lines, so
  SRT, VTT, and TXT output read as clean, non-overlapping text.
- TXT `paragraphs` and `sentences` layouts use a simple punctuation heuristic (with a short abbreviation list in `app/convert.py`), so captions without punctuation stay as one long sentence and an unusual abbreviation can occasionally split a sentence early; use `layout=cues` for the raw caption lines.
- The info cache is per process and in memory. With several workers each keeps its own cache.
- Only download subtitles you have the right to use, and respect YouTube's Terms of Service.
