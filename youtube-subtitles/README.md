---
title: YouTube Subtitle Downloader
emoji: ⬇️
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# YouTube Subtitle Downloader

A small self-hosted web app that lists the subtitle tracks of a YouTube video and downloads them as
SRT, VTT, or plain text. The backend is FastAPI plus [yt-dlp](https://github.com/yt-dlp/yt-dlp); the
frontend is a single static HTML page (`static/index.html`) with no build step.

Paste a video URL, pick a manual or auto-generated track, choose a format, and download or preview it.
Nothing is stored on disk: video info and downloaded caption text are kept in memory for 10 minutes, so
the lookup, a preview, a download, and a summary prompt on the same track share one yt-dlp extraction and
one caption download.

## Layout

```
app/main.py       FastAPI app: API endpoints, /mcp, serves static/
app/mcp_server.py MCP server (tools get_video_info with the menu app, get_subtitles, get_download_link)
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

`/api/info` also returns `original_language` (the spoken language, from yt-dlp or the `<lang>-orig` caption
track, or null) and a `translated` flag per track (true for auto tracks YouTube machine-translates on request).

Track names are normalized: YouTube's "(Original)" and "(auto-generated)" are removed, so the `en-orig`
auto track is just "English"; the `lang` code and the `auto` flag carry the distinction. File headers
read `Subtitles: English (en, original)` for manual (uploader-provided) tracks and
`Subtitles: English (en-orig, auto)` for auto tracks.

Errors are JSON `{"detail": "..."}`: 400 for an invalid URL or parameter, 404 when the video (or the
requested track) has no subtitles, 502 when yt-dlp fails (private, removed, geo-blocked, bot check,
network).

Accepted inputs: `youtube.com/watch?v=`, `youtu.be/<id>`, `/shorts/<id>`, `/live/<id>`, `/embed/<id>`,
`m.youtube.com`, `music.youtube.com`, or a bare 11-character video id. The server only ever passes a
rebuilt `https://www.youtube.com/watch?v=<id>` URL to yt-dlp, never the raw user input.

## Use it from Claude and ChatGPT (MCP)

The app also serves an MCP (Model Context Protocol) server at `https://<your-host>/mcp` (streamable
HTTP with sessions: clients send `initialize`, then the returned `Mcp-Session-Id` header on every request;
sessions live in the server process, so run a single worker, and idle ones expire after 30 minutes) with
three tools: `get_video_info` (video facts, downloadable tracks, a recommended one, and the interactive
menu, below), `get_subtitles` (text, SRT, or VTT with the metadata header; long output is truncated at `max_chars`) and
`get_download_link` (a clickable `/api/download` link for the chosen track, format and layout, without
fetching the subtitles; it uses `PUBLIC_BASE_URL`, else `RENDER_EXTERNAL_URL`, else the request's host).
When you paste only a link, the model calls `get_video_info` and waits for your choice. The menu lives on
`get_video_info` itself because some clients load connector tools lazily through a keyword search and
would never see a separate menu tool.

`get_video_info` is an MCP App: clients that render MCP Apps (Claude, for example) show an interactive
menu in the chat with the subtitle tracks, the format (TXT, SRT, VTT), the TXT text layout, and buttons to
download the file, preview the text, summarize, translate to Korean, or list the key points. The view is
the `ui://youtube-subtitles/menu.html` resource, served from `app/ui/menu.html`; it may load the video
thumbnail from `i.ytimg.com`. Its structured result carries both the fields the model reads (title,
tracks, recommended, options) and the data the view reads (video, formats, layouts, labels, the download
link template). The server instructions tell the model to call `get_video_info` at once, before asking the
user anything, because only the tool result reveals whether the host rendered the menu; a model that asks
first would show its own question list instead of the menu. Clients that declare no MCP Apps support get
the same menu as text, and the model asks in
two steps: first what to do (download the subtitle file, summary, translation, key points), then, only for
a download, which track and format. An explicit request such as "summarize this video" skips the questions. The built `app/ui/menu.html` is committed, so running the app needs no Node.js;
after changing the view sources in `ui/`, rebuild it with `cd ui && npm install && npm run build`
(Node 22). If the file is missing, the server logs a warning and serves a placeholder page.

- **Summary report (.md)** asks Claude to fetch the subtitles and write its own structured summary (overview, key arguments, paraphrased quotes, timeline, takeaways) as a downloadable Markdown file named after the video, plus a three-line summary in the chat.
- **Add to chat** fetches the selected track as TXT paragraphs and places it in the chat composer as a user message (`ui/message`) with a short instruction; the user presses send, and later questions use the transcript without another tool call. Claude Desktop accepts `ui/update-model-context` but the model did not see content sent that way, so the app does not use it.
- The prompt buttons (Summarize, Translate, Key points, Summary report) fill the chat composer; the user sends the message and Claude then calls `get_subtitles`. The server answers that call from its 10-minute caption cache, so YouTube is not contacted again for a track already previewed or downloaded. After **Add to chat** for the same track, these prompts instead tell Claude to use the transcript already in the conversation and to fetch only if it is missing.
- An expand button appears in the header only on hosts that list `fullscreen` in `availableDisplayModes` (Claude Desktop does); it toggles between the inline card and the host's full-screen view. The menu is an MCP App rendered by the host and cannot be turned into a Claude artifact.

Verified in Claude Desktop: the menu card, Download (saves the file), Preview, the prompt buttons (they fill
the composer; Desktop shows its usual "use caution before running this prompt" banner, which is normal),
Summary report (.md) producing a Markdown file, Add to chat, and the expand button.

Claude Desktop treats every tool that renders an MCP App as a third-party app that needs a one-time opt-in
per conversation: with a bare link it may show a "Your connectors" card and wait, and pressing "Use" alone
does not run the tool until you send another message. Naming the connector in the first message, for
example "Use YouTube Subtitles for this: https://youtu.be/<id>", opens the menu directly; later links in
the same conversation need no name. Renaming the connector to something short in Desktop's settings makes
that easier to type. The server cannot bypass this rule.

The server enforces that flow: `get_subtitles` and `get_download_link` return an error unless the call
passes `user_confirmed: true` (set only once the user picked an action in the menu, answered the questions, or
asked explicitly) and `get_video_info` was already called for that video (remembered process-wide, not per
MCP session: on claude.ai in a browser the menu's tool calls arrive on a different session than the model's
lookup), so a model that jumps straight to fetching is told to show the menu and ask first. The menu view
passes `user_confirmed: true` itself; the `subtitles://` resources are not gated.

`get_subtitles` also attaches the track as an embedded resource and a resource link named like the download
file, so clients that support it show a file; pass `attach: false` for the text only.
Tracks are readable as resources at `subtitles://video/{video_id}/{lang}/{auto}/{fmt}/{layout}{?header}` (for example
`subtitles://video/dQw4w9WgXcQ/en/false/txt/paragraphs`), and `subtitles://recent` lists recently looked-up videos.

Set `MCP_API_KEY` on the host, then use `https://<your-host>/mcp?key=<value>` as the URL (or send
`Authorization: Bearer <value>` where the client supports custom headers).

- claude.ai (web and mobile apps): Settings > Connectors > Add custom connector, paste the URL.
- Claude Desktop: the same Connectors settings (or Developer > Edit Config with an http server entry).
- Claude Code: `claude mcp add --transport http youtube-subtitles "https://<host>/mcp?key=<value>"`
- ChatGPT: Settings > Connectors > Advanced > Developer mode > Create, paste the URL. Or build a Custom
  GPT Action by importing `https://<host>/openapi.json` (set `PUBLIC_BASE_URL` on the server first).

Example prompts: "Use YouTube Subtitles for this: https://youtu.be/<id>" (opens the menu) or "Get the
subtitles of https://youtu.be/<id> and summarize them in Korean" (explicit, no questions). Machine-
translated YouTube captions are rate-limited and are not offered, so the tools return the original
language; the model translates the text itself.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `YTDLP_COOKIES` | Path to a Netscape-format `cookies.txt`, passed to yt-dlp as `cookiefile`. Helps with "Sign in to confirm you're not a bot". Takes precedence over `YTDLP_COOKIES_CONTENT`. |
| `YTDLP_COOKIES_CONTENT` | The full text of a Netscape-format `cookies.txt` (for hosts where you can only set secrets, not files). Written once per process to a private temp file (mode 0600). Literal `\n` sequences are turned back into newlines if the secrets UI flattened them. |
| `YTDLP_PROXY` | Proxy URL for yt-dlp, for example `http://user:pass@host:port` or `socks5://host:1080`. |
| `MCP_API_KEY` | Secret required on every `/mcp` request (as `?key=`, `Authorization: Bearer`, or `X-API-Key`). Unset means the MCP endpoint is open to anyone (a warning is logged at startup). The web UI and REST API never need it. |
| `PUBLIC_BASE_URL` | Public URL of the deployment, for example `https://user-youtube-subtitles.hf.space`. Listed as the server in `/openapi.json` so it can be imported into a Custom GPT Action, and used by the MCP tool `get_download_link` to build full download links. |
| `PORT` | Listening port inside the Docker image (default 7860). |
| `POT_PROVIDER_URL` | Where the PO token provider listens (default `http://127.0.0.1:4416`, started by `start.sh` in the Docker image). |
| `TOKEN_TTL` | Hours the provider caches a PO token (default 6). |

## Free deployment

### Your own Mac plus Tailscale Funnel (recommended when you have an always-on Mac)

Running on a home network avoids YouTube's data-center IP blocks entirely, and Tailscale Funnel gives the
app a stable public HTTPS address for free.

1. Install Tailscale from https://tailscale.com/download/mac and sign in.
2. Publish port 7860: `/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg 7860`. The printed
   `https://<mac-name>.<tailnet>.ts.net/` is your web app; `/mcp?key=<MCP_API_KEY>` on it is the MCP URL.
   The Funnel setting persists across reboots as long as Tailscale starts at login.
3. Register the app as a LaunchAgent (starts at login, restarts on crash), from `youtube-subtitles/` with
   the virtualenv created:
   `scripts/mac/install_launch_agent.sh <MCP_API_KEY> 7860 https://<mac-name>.<tailnet>.ts.net`.
   The third argument (the Funnel URL) is optional but recommended: it becomes `PUBLIC_BASE_URL`, so the
   download links the MCP tools hand out always point at your public address.
4. Keep the Mac awake (System Settings > Energy or Displays > prevent automatic sleeping) and enable
   automatic login so the LaunchAgent starts without a user at the keyboard.

Logs: `~/Library/Logs/youtube-subtitles.log`. Update: `git pull` in the repo, then
`launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles`.

Rotate the MCP key: run the install script again with the new key (any length works; 20 or more random
letters and digits is a sensible minimum), then change the `X-API-Key` header in the Claude connector. The
old key stops working the moment the app restarts. If the script ends with
`Bootstrap failed: 5: Input/output error`, launchd had not finished removing the old job; the plist is
already written, so run `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.youtube-subtitles.plist`
followed by `launchctl kickstart -k gui/$(id -u)/com.youtube-subtitles`.


### Hugging Face Spaces (Docker, requires a PRO subscription)

Hugging Face no longer allows Docker Spaces on free accounts (the API answers 402 Payment Required).
If you have PRO:

Fastest path, all from the terminal (only the token is created in the browser):

```bash
pip install -U "huggingface_hub[cli]"
hf auth login        # paste a WRITE token from https://huggingface.co/settings/tokens
python scripts/deploy_hf_space.py --space <hf-username>/youtube-subtitles
```

The script creates a private Docker Space, sets `MCP_API_KEY` (generated and printed once) and
`PUBLIC_BASE_URL`, uploads the app, and prints the web and MCP URLs. Re-run it to redeploy; add
`--cookies-file cookies.txt` to store cookies, `--public` for a public Space.

Manual setup and the GitHub sync workflow:

This README already starts with the Space front matter (`sdk: docker`, `app_port: 7860`), so the folder
can be used as the Space repository as is. A GitHub Actions workflow
(`.github/workflows/sync-hf-space.yml` at the repository root) keeps the Space in sync:

1. Create a new Space on Hugging Face and choose the **Docker** SDK (blank template).
2. Create a token with **write** access at https://huggingface.co/settings/tokens.
3. In the GitHub repository, go to Settings, Secrets and variables, Actions and add:
   - secret `HF_TOKEN`: the write token;
   - variable `HF_SPACE`: `<hf-username>/<space-name>`.
4. Every push to `main` that touches `youtube-subtitles/` now pushes this folder as the root of the
   Space repository (its default branch is `main`), and the Space rebuilds. You can also run the
   workflow manually from the Actions tab (Sync Hugging Face Space, Run workflow). Without the secret
   or the variable the workflow skips with a notice.
5. Optional: add `YTDLP_COOKIES_CONTENT` (as a Secret) or `YTDLP_PROXY` under the Space's Settings,
   Variables and secrets. See "If YouTube blocks the server" below.

The sync force-pushes, so edit the app here on GitHub, not in the Space. Spaces expects port 7860 and
uid 1000, which the Dockerfile already uses.

### Render (free tier, recommended)

A Blueprint file (`render.yaml` at the repository root) describes the service, so setup is a few clicks:

1. Sign up at https://render.com with your GitHub account and allow access to this repository.
2. Dashboard: **New > Blueprint**, pick the repository, keep branch `main`, click **Apply**.
3. Render builds the Dockerfile (5 to 8 minutes the first time) and gives you
   `https://youtube-subtitles-<hash>.onrender.com`. `MCP_API_KEY` is generated automatically; read it
   under the service's **Environment** tab. The MCP URL is `<service url>/mcp?key=<that value>`.
4. `PUBLIC_BASE_URL` is optional on Render: the app uses Render's `RENDER_EXTERNAL_URL` for
   `/openapi.json`. Add `YTDLP_COOKIES_CONTENT` under Environment if YouTube blocks the server.

The free tier sleeps after 15 idle minutes, so the first request after a pause takes 30 to 60 seconds.
Every push to `main` redeploys automatically.

## If YouTube blocks the server (Sign in to confirm you're not a bot)

1. Install the "Get cookies.txt LOCALLY" browser extension.
2. Sign in to YouTube in that browser (prefer a throwaway account), open youtube.com, and export
   `cookies.txt` in Netscape format with the extension.
3. Paste the entire file content into the environment variable `YTDLP_COOKIES_CONTENT` (Render:
   Environment; Hugging Face: Settings, Variables and secrets, as a **Secret**). Alternatively, mount
   the file and set `YTDLP_COOKIES` to its path.
4. Restart the service.

Cookies expire after a few weeks or months; when the error comes back, export them again. A residential
proxy (`YTDLP_PROXY`) is another option. Running locally on your own computer (for example a Mac) usually
does not need cookies at all.

## Caveats

- **PO tokens on cloud IPs.** From data-center addresses (Render, and often when signed in with cookies)
  YouTube marks caption URLs so that yt-dlp drops every subtitle unless it can present a PO token. The
  Docker image therefore bundles the [bgutil PO token provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
  (a small Node service on 127.0.0.1:4416, about 100 MB of memory) and the matching yt-dlp plugin;
  `start.sh` launches it before uvicorn and `/healthz` reports `pot_provider.reachable`. When the
  provider is missing, `/api/info` answers 404 with a message that names the PO token problem instead of
  "no subtitles". Local runs on a home network normally do not need it; to run it locally anyway:
  `docker run --name bgutil-provider -d --init -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider`.

- YouTube often blocks requests from cloud and datacenter IPs with a "Sign in to confirm you're not a
  bot" check. The API then returns 502 with that message. See the next section.
- yt-dlp must stay current as YouTube changes. Rebuild the image (or `pip install -U "yt-dlp[default,deno]"`)
  when extraction starts failing. The `deno` extra provides the JavaScript runtime that recent yt-dlp
  versions use for YouTube.
- YouTube rate-limits machine-translated auto captions (tracks with `translated: true`, for example
  "Korean auto" on an English video) with HTTP 429. The server retries a few times (2, 4, 8 s), then
  returns 502 with advice. Workaround: download the original-language track (for example `en-orig`)
  and translate it separately, or set fresh cookies with `YTDLP_COOKIES_CONTENT`.
- Auto-generated captions use "rolling" cues on YouTube. The converter removes the repeated lines, so
  SRT, VTT, and TXT output read as clean, non-overlapping text.
- TXT `paragraphs` and `sentences` layouts use a simple punctuation heuristic (with a short abbreviation list in `app/convert.py`), so captions without punctuation stay as one long sentence and an unusual abbreviation can occasionally split a sentence early; use `layout=cues` for the raw caption lines.
- The info and caption caches are per process and in memory. With several workers each keeps its own cache.
- Only download subtitles you have the right to use, and respect YouTube's Terms of Service.
