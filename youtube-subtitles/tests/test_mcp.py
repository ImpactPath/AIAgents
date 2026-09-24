"""MCP endpoint tests: raw JSON-RPC over streamable HTTP (sessions, JSON responses), yt-dlp mocked.

Every test opens a session like a real client: initialize, read the Mcp-Session-Id response header,
send notifications/initialized, then pass the header (and MCP-Protocol-Version) on every request.
"""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import youtube
from app.main import app
from app.youtube import Track, VideoInfo

VID = "dQw4w9WgXcQ"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
PROTOCOL = "2025-06-18"
APP_MIME = "text/html;profile=mcp-app"
APPS_CAPABILITY = {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": [APP_MIME]}}}


def full_tracks():
    return [
        Track("de", "German", False, "m-de"),
        Track("en", "English", False, "m-en"),
        Track("fr", "French", False, "m-fr"),
        Track("ko", "Korean", False, "m-ko"),
        Track("en", "English", True, "a-en"),
        Track("en-orig", "English", True, "a-en-orig"),
        Track("es-orig", "Spanish", True, "a-es-orig"),
        Track("ko", "Korean", True, "a-ko", translated=True),
    ]


def make_info(tracks=None, original="en"):
    return VideoInfo(VID, "Never Gonna Give You Up", "Rick Astley", 212, None,
                     full_tracks() if tracks is None else tracks, "2009-10-25", original)


def caption_vtt(track, cues=1):
    kind = "auto" if track.auto else "manual"
    body = "".join(
        f"00:00:{i:02d}.000 --> 00:00:{i:02d}.900\nLine {i} from {track.lang} {kind}.\n\n" for i in range(cues)
    )
    return "WEBVTT\n\n" + body


@pytest.fixture
def info_holder(monkeypatch):
    holder = {"info": make_info(), "fetched": [], "cues": 1}
    monkeypatch.setattr(youtube, "fetch_info", lambda vid: holder["info"])

    def fake_sub(track):
        holder["fetched"].append((track.lang, track.auto))
        return caption_vtt(track, holder["cues"])

    monkeypatch.setattr(youtube, "fetch_subtitle_text", fake_sub)
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    return holder


@pytest.fixture
def client(info_holder):
    with TestClient(app) as c:
        c.mcp_session = open_session(c)
        yield c


def open_session(client, capabilities=None, *, protocol=PROTOCOL, path="/mcp", **kwargs):
    """initialize, then notifications/initialized; returns the headers to send on the session's requests."""
    r = client.post(path, json={
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": protocol, "capabilities": capabilities or {},
                   "clientInfo": {"name": "pytest", "version": "1"}},
    }, headers=HEADERS, **kwargs)
    assert r.status_code == 200, r.text
    session = {"Mcp-Session-Id": r.headers["mcp-session-id"], "MCP-Protocol-Version": protocol}
    note = client.post(path, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers={**HEADERS, **session}, **kwargs)
    assert note.status_code == 202, note.text
    return session


def rpc(client, method, params=None, *, id=1, path="/mcp", headers=None, session=True, **kwargs):
    """POST one JSON-RPC request; on the client's session unless it is initialize or session=False."""
    payload = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        payload["params"] = params
    if session and method != "initialize":
        headers = {**getattr(client, "mcp_session", {}), **(headers or {})}
    return client.post(path, json=payload, headers={**HEADERS, **(headers or {})}, **kwargs)


def call(client, name, *, headers=None, **arguments):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def pick(client, name, *, headers=None, **arguments):
    """The flow the server enforces: get_video_info on the session, then `name` with user_confirmed=true."""
    call(client, "get_video_info", headers=headers, url=arguments.get("url", VID))
    return call(client, name, headers=headers, user_confirmed=True, **arguments)


USER_CONFIRMED_DESCRIPTION = (
    "Set true only after the user explicitly chose what to do: they picked an action in the subtitle menu, "
    "answered your question, or their request itself was explicit (for example 'summarize this video'). "
    "When the user only shared a link, leave it false, call get_video_info, and ask."
)
NOT_CONFIRMED = "The user has not chosen yet. Call get_video_info first (it shows the subtitle menu)"
NOT_LOOKED_UP = "Call get_video_info for this video first so the user can see the tracks and choose."


def text_of(result):
    return result["content"][0]["text"]


def test_initialize_and_tools_list(client):
    r = rpc(client, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
    })
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    session_id = r.headers["mcp-session-id"]
    assert session_id and session_id != client.mcp_session["Mcp-Session-Id"]
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "youtube-subtitles"
    instructions = result["instructions"]
    assert "call get_video_info first" in instructions and "get_download_link" in instructions
    assert "ask the user in two steps before fetching anything" in instructions
    assert "skip the questions and call get_subtitles" in instructions and "subtitle_menu" not in instructions
    assert "translate the text yourself" in instructions
    note = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers={**HEADERS, "Mcp-Session-Id": session_id})
    assert note.status_code == 202

    tools = {t["name"]: t for t in rpc(client, "tools/list", id=2).json()["result"]["tools"]}
    assert set(tools) == {"get_video_info", "get_subtitles", "get_download_link"}
    info_tool, subs_tool = tools["get_video_info"], tools["get_subtitles"]
    assert info_tool["inputSchema"]["required"] == ["url"] and set(info_tool["inputSchema"]["properties"]) == {"url"}
    assert {"tracks", "recommended", "original_language", "published"} <= set(info_tool["outputSchema"]["properties"])
    props = subs_tool["inputSchema"]["properties"]
    assert subs_tool["inputSchema"]["required"] == ["url"]
    assert props["fmt"]["enum"] == ["txt", "srt", "vtt"] and props["fmt"]["default"] == "txt"
    assert props["layout"]["enum"] == ["paragraphs", "sentences", "cues"]
    assert props["max_chars"]["default"] == 200000 and props["include_header"]["default"] is True
    assert "rate-limited" in subs_tool["description"] and not subs_tool["description"].startswith(" ")
    assert props["attach"]["default"] is True and "attaches the file as a resource" in subs_tool["description"]
    assert {"options", "menu_hint"} <= set(info_tool["outputSchema"]["required"])
    link_tool = tools["get_download_link"]
    link_props = link_tool["inputSchema"]["properties"]
    assert link_tool["inputSchema"]["required"] == ["url"] and "ctx" not in link_props
    assert set(link_props) == {"url", "user_confirmed", "lang", "auto", "fmt", "layout", "include_header"}
    assert link_props["fmt"]["enum"] == ["txt", "srt", "vtt"] and link_props["layout"]["default"] == "paragraphs"
    assert {"download_url", "web_app_url", "filename", "track", "note"} <= set(link_tool["outputSchema"]["properties"])
    for tool in (subs_tool, link_tool):
        assert tool["description"].startswith("Requires user_confirmed=true; see that parameter. ")
        confirmed = tool["inputSchema"]["properties"]["user_confirmed"]
        assert confirmed["type"] == "boolean" and confirmed["default"] is False
        assert confirmed["description"] == USER_CONFIRMED_DESCRIPTION
    assert ("get_subtitles and get_download_link refuse to run until user_confirmed=true and get_video_info was "
            "called for that video in this session.") in instructions


def test_mcp_path_without_and_with_trailing_slash(client):
    for path in ("/mcp", "/mcp/"):
        r = rpc(client, "tools/list", path=path, follow_redirects=False)
        assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 3


def test_get_video_info_filters_and_orders(client):
    result = call(client, "get_video_info", url=f"https://youtu.be/{VID}")
    assert result["isError"] is False
    data = result["structuredContent"]
    assert [(t["lang"], t["auto"]) for t in data["tracks"]] == [
        ("en", False), ("ko", False), ("de", False), ("fr", False), ("en-orig", True), ("es-orig", True),
    ]
    assert data["tracks"][0] == {"lang": "en", "name": "English", "auto": False, "translated": False,
                                 "kind": "original"}
    assert data["recommended"] == {"lang": "en", "auto": False}
    assert data["duration"] == "03:32" and data["duration_seconds"] == 212
    assert data["published"] == "2009-10-25" and data["original_language"] == "en"
    assert data["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert [f["id"] for f in data["options"]["formats"]] == ["txt", "srt", "vtt"]
    assert data["options"]["formats"][0] == {"id": "txt", "label": "Plain text"}
    assert [x["id"] for x in data["options"]["layouts"]] == ["paragraphs", "sentences", "cues"]
    assert data["options"]["actions"] == ["download_link", "summary", "translation", "key_points"]
    assert "wait" in data["menu_hint"] and "two steps" in data["menu_hint"]


def test_get_video_info_without_orig_track_keeps_one_auto(client, info_holder):
    info_holder["info"] = make_info([
        Track("de", "German", True, "a-de", translated=True),
        Track("en", "English", True, "a-en"),
        Track("ko", "Korean", True, "a-ko", translated=True),
    ])
    data = call(client, "get_video_info", url=VID)["structuredContent"]
    assert [(t["lang"], t["auto"]) for t in data["tracks"]] == [("en", True)]
    assert data["recommended"] == {"lang": "en", "auto": True}


def test_get_subtitles_default_track_with_header(client, info_holder):
    result = pick(client, "get_subtitles", url=VID)
    assert result["isError"] is False and "structuredContent" not in result
    text = text_of(result)
    assert text.startswith("Title: Never Gonna Give You Up\nChannel: Rick Astley\n")
    assert "Subtitles: English (en, original)\n\nLine 0 from en manual.\n" in text
    assert info_holder["fetched"] == [("en", False)]


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"lang": "ko"}, ("ko", False)),
        ({"lang": "ko", "auto": True}, ("ko", True)),
        ({"lang": "en", "auto": True}, ("en", True)),
        ({"lang": "es"}, ("es-orig", True)),
        ({"lang": "en-orig"}, ("en-orig", True)),
    ],
)
def test_get_subtitles_explicit_lang(client, info_holder, args, expected):
    result = pick(client, "get_subtitles", url=VID, **args)
    assert result["isError"] is False
    assert info_holder["fetched"] == [expected]


def test_get_subtitles_formats_and_layouts(client, info_holder):
    info_holder["cues"] = 3
    srt = text_of(pick(client, "get_subtitles", url=VID, fmt="srt"))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:00,001\nTitle: ") and "\n\n2\n00:00:00,000 --> " in srt
    vtt = text_of(pick(client, "get_subtitles", url=VID, fmt="vtt", include_header=False))
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> ")
    sentences = text_of(pick(client, "get_subtitles", url=VID, layout="sentences", include_header=False))
    assert sentences == "Line 0 from en manual.\nLine 1 from en manual.\nLine 2 from en manual.\n"
    paragraphs = text_of(pick(client, "get_subtitles", url=VID, include_header=False))
    assert paragraphs == "Line 0 from en manual. Line 1 from en manual. Line 2 from en manual.\n"
    bad = pick(client, "get_subtitles", url=VID, fmt="docx")
    assert bad["isError"] is True


def test_get_subtitles_truncates_at_line_boundary(client, info_holder):
    info_holder["cues"] = 40
    full = text_of(pick(client, "get_subtitles", url=VID, layout="sentences"))
    text = text_of(pick(client, "get_subtitles", url=VID, layout="sentences", max_chars=300))
    kept, _, marker = text.rpartition("\n")
    assert len(kept) <= 300 and full.startswith(kept + "\n")
    assert marker == f"[truncated: {len(full) - len(kept)} more characters]"
    assert text_of(pick(client, "get_subtitles", url=VID, max_chars=10**6)) == text_of(pick(client, "get_subtitles", url=VID))


def link_query(download_url):
    parts = urlsplit(download_url)
    return parts, {k: v[0] for k, v in parse_qs(parts.query).items()}


def test_get_download_link_default_track_from_public_base_url(client, info_holder, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://dukwoos-mac-mini.tailb8572b.ts.net/")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://ignored.onrender.com")
    result = pick(client, "get_download_link", url=f"https://www.youtube.com/watch?v={VID}&t=5")
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["download_url"] == (
        "https://dukwoos-mac-mini.tailb8572b.ts.net/api/download"
        f"?url={VID}&lang=en&auto=false&fmt=txt&layout=paragraphs&header=1"
    )
    assert data["web_app_url"] == "https://dukwoos-mac-mini.tailb8572b.ts.net/"
    assert data["filename"] == "Never Gonna Give You Up.en.txt"
    assert data["track"] == {"lang": "en", "name": "English", "auto": False}
    assert "no login" in data["note"]
    assert info_holder["fetched"] == []  # the link tool never downloads the subtitles


def test_get_download_link_explicit_choice_encoding_and_filename(client, info_holder, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example")
    info_holder["info"] = make_info([Track("pt-BR", "Portugu\u00eas (Brasil)", False, "m-pt"),
                                     Track("en-orig", "English", True, "a-en-orig")])
    info_holder["info"].title = "Caf\u00e9 & Co: 100% <live>?"
    data = pick(client, "get_download_link", url=VID, lang="pt-BR", fmt="txt", layout="sentences",
                include_header=False)["structuredContent"]
    parts, q = link_query(data["download_url"])
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "host.example", "/api/download")
    assert q == {"url": VID, "lang": "pt-BR", "auto": "false", "fmt": "txt", "layout": "sentences", "header": "0"}
    assert data["filename"] == "Caf\u00e9 & Co 100% live.pt-BR.txt"

    data = pick(client, "get_download_link", url=VID, lang="en", fmt="srt", layout="cues")["structuredContent"]
    _, q = link_query(data["download_url"])
    assert q == {"url": VID, "lang": "en-orig", "auto": "true", "fmt": "srt", "layout": "paragraphs", "header": "1"}
    assert data["track"] == {"lang": "en-orig", "name": "English", "auto": True}
    assert data["filename"] == "Caf\u00e9 & Co 100% live.en-orig.auto.srt"

    missing = pick(client, "get_download_link", url=VID, lang="ja")
    assert missing["isError"] is True and "No subtitle track for language 'ja'" in text_of(missing)


def test_get_download_link_url_is_served_by_rest_api(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example")
    data = pick(client, "get_download_link", url=VID, lang="ko", fmt="vtt")["structuredContent"]
    parts = urlsplit(data["download_url"])
    r = client.get(f"{parts.path}?{parts.query}")
    assert r.status_code == 200 and r.text.startswith("WEBVTT")
    assert data["filename"].encode("ascii", "ignore").decode() in r.headers["content-disposition"]


@pytest.mark.parametrize("args", [{"fmt": "docx"}, {"layout": "words"}, {"url": "not a url"}])
def test_get_download_link_rejects_bad_arguments(client, args):
    result = pick(client, "get_download_link", **{"url": VID, **args})
    assert result["isError"] is True


def test_get_download_link_base_url_from_request(client, monkeypatch):
    data = pick(client, "get_download_link", url=VID)["structuredContent"]
    assert data["download_url"].startswith("http://testserver/api/download?url=")
    assert data["web_app_url"] == "http://testserver/" and "no login" in data["note"]

    r = rpc(client, "tools/call", {"name": "get_download_link", "arguments": {"url": VID, "user_confirmed": True}},
            headers={"X-Forwarded-Proto": "https", "Host": "dukwoos-mac-mini.tailb8572b.ts.net"})
    assert r.json()["result"]["structuredContent"]["web_app_url"] == "https://dukwoos-mac-mini.tailb8572b.ts.net/"

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://yt.onrender.com")
    data = pick(client, "get_download_link", url=VID)["structuredContent"]
    assert data["web_app_url"] == "https://yt.onrender.com/"


def test_get_download_link_relative_without_any_base_url(info_holder):
    """Outside an HTTP request (stdio, direct call) and without env vars the link is relative."""
    import anyio
    from mcp.server.mcpserver import Context

    from app.mcp_server import UNCONFIGURED_NOTE, get_download_link, get_video_info

    anyio.run(lambda: get_video_info(VID, Context()))
    data = anyio.run(lambda: get_download_link(VID, Context(), user_confirmed=True, fmt="srt"))
    assert data["download_url"] == f"/api/download?url={VID}&lang=en&auto=false&fmt=srt&layout=paragraphs&header=1"
    assert data["web_app_url"] == "/" and data["note"] == UNCONFIGURED_NOTE
    assert "PUBLIC_BASE_URL" in data["note"] and info_holder["fetched"] == []


@pytest.mark.parametrize(
    "tool,args,message",
    [
        ("get_video_info", {"url": "https://example.com/watch?v=dQw4w9WgXcQ"}, youtube.INVALID_URL),
        ("get_subtitles", {"url": "not a url"}, youtube.INVALID_URL),
        ("get_subtitles", {"url": VID, "lang": "ja"}, "No subtitle track for language 'ja'"),
        ("get_subtitles", {"url": VID, "lang": "de", "auto": True}, "No auto-generated subtitle track"),
    ],
)
def test_tool_errors(client, tool, args, message):
    result = call(client, tool, **args) if tool == "get_video_info" else pick(client, tool, **args)
    assert result["isError"] is True and message in text_of(result)


def test_tool_error_for_no_subtitles_and_ytdlp_failure(client, info_holder, monkeypatch):
    info_holder["info"] = make_info(tracks=[])
    assert youtube.NO_SUBTITLES in text_of(call(client, "get_video_info", url=VID))

    def boom(vid):
        raise youtube.YoutubeError("[youtube] x: Video unavailable")

    monkeypatch.setattr(youtube, "fetch_info", boom)
    result = call(client, "get_video_info", url=VID)
    assert result["isError"] is True and "Could not fetch video info: [youtube] x: Video unavailable" in text_of(result)


def test_translated_429_message_surfaces(client, monkeypatch):
    def rate_limited(track):
        raise youtube.YoutubeError(youtube.RATE_LIMITED_TRANSLATED)

    monkeypatch.setattr(youtube, "fetch_subtitle_text", rate_limited)
    result = pick(client, "get_subtitles", url=VID, lang="ko", auto=True)
    assert result["isError"] is True and "machine-translated captions (HTTP 429)" in text_of(result)


def test_api_key_guard(client, monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cret-key")
    denied = rpc(client, "tools/list")
    assert denied.status_code == 401 and denied.json() == {"detail": "Missing or invalid MCP API key."}
    assert rpc(client, "tools/list", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert rpc(client, "tools/list", path="/mcp?key=wrong").status_code == 401
    assert rpc(client, "tools/list", headers={"Authorization": "Basic s3cret-key"}).status_code == 401
    for kwargs in (
        {"headers": {"Authorization": "Bearer s3cret-key"}},
        {"headers": {"authorization": "bearer s3cret-key"}},
        {"headers": {"X-API-Key": "s3cret-key"}},
        {"path": "/mcp?key=s3cret-key"},
    ):
        r = rpc(client, "tools/list", **kwargs)
        assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 3, kwargs
    # The rest of the site ignores the key.
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/info", params={"url": "not-a-url"}).status_code == 400
    assert client.get("/api/info", params={"url": VID}).status_code == 200
    assert client.get("/api/download", params={"url": VID, "lang": "en"}).status_code == 200
    assert client.get("/").status_code in (200, 404)


def test_openapi_servers_and_operation_ids(client, monkeypatch):
    spec = client.get("/openapi.json").json()
    assert "servers" not in spec or spec["servers"] == []
    ops = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert ops == {"get_video_info", "download_subtitles"}
    assert spec["paths"]["/api/download"]["get"]["summary"] == "Download subtitles"

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://user-youtube-subtitles.hf.space/")
    spec = client.get("/openapi.json").json()
    assert spec["servers"] == [{"url": "https://user-youtube-subtitles.hf.space"}]


def test_app_can_start_twice(info_holder):
    for _ in range(2):
        with TestClient(app) as c:
            c.mcp_session = open_session(c)
            assert rpc(c, "tools/list").status_code == 200


def test_resources_listed_and_templates(client):
    r = rpc(client, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
    })
    result = r.json()["result"]
    assert "resources" in result["capabilities"]
    assert "attaches the file as a resource" in result["instructions"]

    templates = rpc(client, "resources/templates/list").json()["result"]["resourceTemplates"]
    by_uri = {t["uriTemplate"]: t for t in templates}
    assert set(by_uri) == {
        f"subtitles://video/{{video_id}}/{{lang}}/{{auto}}/{fmt}/{{layout}}{{?header}}" for fmt in ("txt", "srt", "vtt")
    }
    assert by_uri["subtitles://video/{video_id}/{lang}/{auto}/txt/{layout}{?header}"]["mimeType"] == "text/plain"
    assert by_uri["subtitles://video/{video_id}/{lang}/{auto}/srt/{layout}{?header}"]["mimeType"] == "application/x-subrip"
    assert by_uri["subtitles://video/{video_id}/{lang}/{auto}/vtt/{layout}{?header}"]["mimeType"] == "text/vtt"

    resources = rpc(client, "resources/list").json()["result"]["resources"]
    assert [(x["uri"], x["mimeType"]) for x in resources] == [
        (MENU_URI, APP_MIME), ("subtitles://recent", "application/json"),
    ]


def read_resource(client, uri):
    r = rpc(client, "resources/read", {"uri": uri})
    assert r.status_code == 200, r.text
    return r.json()


def test_read_subtitles_resource(client, info_holder):
    body = read_resource(client, f"subtitles://video/{VID}/en/false/txt/paragraphs")
    [contents] = body["result"]["contents"]
    assert contents["uri"] == f"subtitles://video/{VID}/en/false/txt/paragraphs"
    assert contents["mimeType"] == "text/plain"
    assert contents["text"] == text_of(pick(client, "get_subtitles", url=VID, lang="en", auto=False))
    assert contents["text"].startswith("Title: Never Gonna Give You Up\n")

    [contents] = read_resource(client, f"subtitles://video/{VID}/en-orig/true/srt/cues?header=0")["result"]["contents"]
    assert contents["mimeType"] == "application/x-subrip"
    assert contents["text"].startswith("1\n00:00:00,000 --> 00:00:00,900\nLine 0 from en-orig auto.")
    [contents] = read_resource(client, f"subtitles://video/{VID}/ko/false/vtt/cues")["result"]["contents"]
    assert contents["mimeType"] == "text/vtt" and contents["text"].startswith("WEBVTT")
    assert info_holder["fetched"][-2:] == [("en-orig", True), ("ko", False)]

    for uri in (f"subtitles://video/{VID}/ja/false/txt/paragraphs", f"subtitles://video/{VID}/en/maybe/txt/paragraphs",
                f"subtitles://video/{VID}/en/false/txt/words"):
        assert "error" in read_resource(client, uri), uri


def test_recent_resource_lists_cached_videos(client):
    youtube.clear_cache()
    empty = read_resource(client, "subtitles://recent")["result"]["contents"][0]
    assert empty["mimeType"] == "application/json" and json.loads(empty["text"]) == {"videos": []}
    youtube._cache_put(VID, make_info())
    try:
        data = json.loads(read_resource(client, "subtitles://recent")["result"]["contents"][0]["text"])
    finally:
        youtube.clear_cache()
    assert data["videos"] == [{
        "video_id": VID, "title": "Never Gonna Give You Up", "channel": "Rick Astley",
        "url": f"https://www.youtube.com/watch?v={VID}",
        "subtitles_uri": f"subtitles://video/{VID}/en/false/txt/paragraphs",
    }]


def test_get_subtitles_attaches_resource_and_link(client, info_holder):
    result = pick(client, "get_subtitles", url=VID, lang="es")
    assert result["isError"] is False and "structuredContent" not in result
    text_block, embedded, link = result["content"]
    assert [b["type"] for b in result["content"]] == ["text", "resource", "resource_link"]
    uri = f"subtitles://video/{VID}/es-orig/true/txt/paragraphs"
    assert embedded["resource"] == {"uri": uri, "mimeType": "text/plain", "text": text_block["text"]}
    assert link["uri"] == uri and link["mimeType"] == "text/plain"
    assert link["name"] == "Never Gonna Give You Up.es-orig.auto.txt"
    assert link["size"] == len(text_block["text"].encode("utf-8"))
    # The attached URI reads back the same text.
    assert read_resource(client, uri)["result"]["contents"][0]["text"] == text_block["text"]

    srt = pick(client, "get_subtitles", url=VID, fmt="srt", layout="sentences", include_header=False)["content"]
    assert srt[1]["resource"]["uri"] == f"subtitles://video/{VID}/en/false/srt/cues?header=0"
    assert srt[1]["resource"]["mimeType"] == srt[2]["mimeType"] == "application/x-subrip"
    assert srt[2]["name"] == "Never Gonna Give You Up.en.srt"

    plain = pick(client, "get_subtitles", url=VID, attach=False)
    assert [b["type"] for b in plain["content"]] == ["text"]
    assert plain["content"][0]["text"] == pick(client, "get_subtitles", url=VID)["content"][0]["text"]


# MCP App: get_video_info shows the menu through its ui:// view.

MENU_URI = "ui://youtube-subtitles/menu.html"
MODEL_FIELDS = {"video_id", "title", "channel", "duration_seconds", "duration", "published", "url",
                "original_language", "tracks", "recommended", "options", "menu_hint"}
VIEW_FIELDS = {"video", "tracks", "recommended", "formats", "layouts", "defaults", "base_url", "download_template",
               "labels"}


def menu_call(client, capabilities, **arguments):
    """get_video_info on a fresh session whose initialize declared `capabilities`."""
    return call(client, "get_video_info", headers=open_session(client, capabilities), **arguments)


def test_get_video_info_listed_with_ui_resource(client):
    tools = {t["name"]: t for t in rpc(client, "tools/list").json()["result"]["tools"]}
    info = tools["get_video_info"]
    assert info["_meta"]["ui"]["resourceUri"] == MENU_URI
    assert info["inputSchema"]["required"] == ["url"] and set(info["inputSchema"]["properties"]) == {"url"}
    assert info["description"].startswith("Look up a YouTube video link: title, channel, duration")
    assert "Call this first whenever the user shares a YouTube URL or video id" in info["description"]
    assert "show the interactive subtitle menu" in info["description"]
    assert MODEL_FIELDS | VIEW_FIELDS <= set(info["outputSchema"]["properties"])
    track_schema = info["outputSchema"]["$defs"]["TrackOut"]["properties"]
    assert {"lang", "name", "auto", "translated", "kind"} == set(track_schema)
    assert "_meta" not in tools["get_subtitles"] and "_meta" not in tools["get_download_link"]


def test_menu_resource_read(client):
    [contents] = read_resource(client, MENU_URI)["result"]["contents"]
    assert contents["uri"] == MENU_URI and contents["mimeType"] == APP_MIME
    assert "ui/initialize" in contents["text"] and "<!DOCTYPE" in contents["text"]
    ui = contents["_meta"]["ui"]
    assert ui["csp"] == {"resourceDomains": ["https://i.ytimg.com", "https://*.ytimg.com"]}
    assert ui["permissions"] == {"clipboardWrite": {}} and ui["prefersBorder"] is True


def test_get_video_info_for_apps_client(client, info_holder, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://host.example/")
    result = menu_call(client, APPS_CAPABILITY, url=f"https://youtu.be/{VID}")
    assert result["isError"] is False
    assert [b["type"] for b in result["content"]] == ["text"]
    assert text_of(result) == (
        "Interactive menu shown for 'Never Gonna Give You Up' (6 tracks: en, ko, de, fr, en-orig (auto), "
        "es-orig (auto)). The user is choosing a track, format and action in the menu. Wait for their choice; "
        "do not call get_subtitles or get_download_link until the user picks an action or asks explicitly."
    )
    data = result["structuredContent"]
    assert set(data) == MODEL_FIELDS | VIEW_FIELDS
    # The model's fields, as get_video_info returned them before the menu moved here.
    assert (data["video_id"], data["title"], data["channel"]) == (VID, "Never Gonna Give You Up", "Rick Astley")
    assert (data["duration_seconds"], data["duration"], data["published"]) == (212, "03:32", "2009-10-25")
    assert data["url"] == f"https://www.youtube.com/watch?v={VID}" and data["original_language"] == "en"
    assert data["options"]["formats"][0] == {"id": "txt", "label": "Plain text"} and "wait" in data["menu_hint"]
    # The view contract.
    assert data["video"] == {
        "video_id": VID, "title": "Never Gonna Give You Up", "channel": "Rick Astley", "duration": "03:32",
        "duration_seconds": 212, "published": "2009-10-25", "url": f"https://www.youtube.com/watch?v={VID}",
        "thumbnail": f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg", "original_language": "en",
    }
    assert data["tracks"] == [
        {"lang": "en", "name": "English", "auto": False, "translated": False, "kind": "original"},
        {"lang": "ko", "name": "Korean", "auto": False, "translated": False, "kind": "original"},
        {"lang": "de", "name": "German", "auto": False, "translated": False, "kind": "original"},
        {"lang": "fr", "name": "French", "auto": False, "translated": False, "kind": "original"},
        {"lang": "en-orig", "name": "English", "auto": True, "translated": False, "kind": "auto"},
        {"lang": "es-orig", "name": "Spanish", "auto": True, "translated": False, "kind": "auto"},
    ]
    assert data["recommended"] == {"lang": "en", "auto": False}
    assert data["formats"] == [{"id": "txt", "label": "TXT"}, {"id": "srt", "label": "SRT"},
                               {"id": "vtt", "label": "VTT"}]
    assert data["layouts"] == [{"id": "paragraphs", "label": "Paragraphs"},
                               {"id": "sentences", "label": "Sentences"},
                               {"id": "cues", "label": "Original cues"}]
    assert data["defaults"] == {"fmt": "txt", "layout": "paragraphs"}
    assert data["base_url"] == "https://host.example"
    assert data["download_template"] == (
        f"https://host.example/api/download?url={VID}&lang={{lang}}&auto={{auto}}&fmt={{fmt}}&layout={{layout}}"
        "&header=1"
    )
    en, ko = data["labels"]["en"], data["labels"]["ko"]
    assert set(en) == set(ko) and len(en) == 21
    assert (en["download"], ko["download"]) == ("Download", "\ub2e4\uc6b4\ub85c\ub4dc")
    assert (en["layout"], ko["translate"]) == ("Text layout", "\ud55c\uad6d\uc5b4\ub85c \ubc88\uc5ed")
    assert info_holder["fetched"] == []  # the menu never downloads subtitles

    # The template fills into a URL the REST API serves.
    url = data["download_template"].format(lang="ko", auto="false", fmt="srt", layout="paragraphs")
    parts = urlsplit(url)
    r = client.get(f"{parts.path}?{parts.query}")
    assert r.status_code == 200 and "Line 0 from ko manual." in r.text


TWO_STEP_END = (
    "Ask the user in two steps before fetching anything: step 1, what to do (download the subtitle file, summary, "
    "translation, key points); step 2, only if they chose download, which subtitle track (list the tracks by name "
    "and code, recommended first) and which format (TXT default, SRT, VTT). When the user's request is already "
    "explicit (for example 'summarize this video'), skip the questions and call get_subtitles with the recommended "
    "track as TXT paragraphs."
)


def test_get_video_info_text_menu_for_client_without_apps(client, info_holder):
    result = menu_call(client, {}, url=VID)
    assert result["isError"] is False and result["structuredContent"]["base_url"] == "http://testserver"
    assert set(result["structuredContent"]) == MODEL_FIELDS | VIEW_FIELDS
    assert text_of(result) == "\n".join([
        f"Subtitle menu for 'Never Gonna Give You Up' (Rick Astley, 03:32, 2009-10-25): "
        f"https://www.youtube.com/watch?v={VID}",
        "",
        "Tracks (pass lang and auto to get_subtitles or get_download_link):",
        "1. English, original: lang=en, auto=false (recommended)",
        "2. Korean, original: lang=ko, auto=false",
        "3. German, original: lang=de, auto=false",
        "4. French, original: lang=fr, auto=false",
        "5. English, auto: lang=en-orig, auto=true",
        "6. Spanish, auto: lang=es-orig, auto=true",
        "",
        "Formats (fmt): TXT (txt), SRT (srt), VTT (vtt); default txt.",
        "Text layout for TXT (layout): Paragraphs (paragraphs), Sentences (sentences), Original cues (cues); "
        "default paragraphs.",
        "Actions: download the subtitle file (get_download_link), preview the text (get_subtitles), summarize, "
        "translate, key points.",
        "",
        TWO_STEP_END,
    ])
    # The default test session declared no apps support either.
    assert text_of(call(client, "get_video_info", url=VID)) == text_of(result)
    # A client that lists the extension without the app MIME type cannot render it either.
    other = {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html"]}}}
    assert text_of(menu_call(client, other, url=VID)) == text_of(result)


def test_get_video_info_without_declared_capabilities(info_holder):
    """No client capabilities at all (a direct call): the text menu plus a note that the view may be shown."""
    import anyio
    from mcp.server.mcpserver import Context

    from app.mcp_server import UNDECLARED_APPS_NOTE, get_video_info

    result = anyio.run(lambda: get_video_info(VID, Context()))
    note, _, menu = result.content[0].text.partition("\n\n")
    assert note == UNDECLARED_APPS_NOTE and "renders MCP Apps" in note
    assert menu.startswith("Subtitle menu for 'Never Gonna Give You Up'")
    assert menu.endswith("\n" + TWO_STEP_END)
    data = result.structured_content
    assert data["base_url"] == "" and data["download_template"].startswith(f"/api/download?url={VID}&lang={{lang}}")
    assert data["tracks"][0] == {"lang": "en", "name": "English", "auto": False, "translated": False,
                                 "kind": "original"}


def test_session_required_and_delete_ends_it(client):
    no_session = rpc(client, "tools/call", {"name": "get_video_info", "arguments": {"url": VID}}, session=False)
    assert no_session.status_code == 400 and "Missing session ID" in no_session.json()["error"]["message"]
    unknown = rpc(client, "tools/list", headers={"Mcp-Session-Id": "not-a-session"})
    assert unknown.status_code == 404

    session = open_session(client, path="/mcp/")
    assert rpc(client, "tools/list", headers=session).status_code == 200
    assert client.delete("/mcp", headers={**HEADERS, **session}).status_code == 200
    gone = rpc(client, "tools/list", headers=session)
    assert gone.status_code == 404 and gone.json()["error"]["message"] == "Session not found"
    assert rpc(client, "tools/list").status_code == 200  # other sessions are unaffected


def test_get_video_info_fetches_info_in_a_worker_thread(client, info_holder, monkeypatch):
    seen = []

    def fake_info(vid):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("worker thread")
        return make_info([Track("en", "English", True, "a-en")], original=None)

    monkeypatch.setattr(youtube, "fetch_info", fake_info)
    result = menu_call(client, APPS_CAPABILITY, url=VID)
    assert seen == ["worker thread"]
    data = result["structuredContent"]
    assert data["tracks"] == [{"lang": "en", "name": "English", "auto": True, "translated": False, "kind": "auto"}]
    assert data["recommended"] == {"lang": "en", "auto": True} and data["video"]["original_language"] is None
    assert "(1 track: en (auto))" in text_of(result)


@pytest.mark.parametrize("url,message", [("not a url", youtube.INVALID_URL), (VID, youtube.NO_SUBTITLES)])
def test_get_video_info_errors_for_apps_client(client, info_holder, url, message):
    info_holder["info"] = make_info(tracks=[])
    result = menu_call(client, APPS_CAPABILITY, url=url)
    assert result["isError"] is True and message in text_of(result)


# The server enforces the flow: user_confirmed=true, and get_video_info for the video in the same session.

@pytest.mark.parametrize("tool", ["get_subtitles", "get_download_link"])
def test_gated_tool_requires_user_confirmed_first(client, info_holder, tool):
    call(client, "get_video_info", url=VID)
    for args in ({}, {"user_confirmed": False}):
        result = call(client, tool, url=VID, **args)
        assert result["isError"] is True and text_of(result) == f"Error executing tool {tool}: " + (
            "The user has not chosen yet. Call get_video_info first (it shows the subtitle menu), ask the user what "
            "they want (step 1: download file, summary, translation or key points; step 2, for downloads: which "
            "track and which format), then call this tool again with user_confirmed=true."
        )
    # user_confirmed is checked before the session gate.
    other = call(client, tool, headers=open_session(client), url=VID)
    assert other["isError"] is True and NOT_CONFIRMED in text_of(other)
    assert info_holder["fetched"] == []


@pytest.mark.parametrize("tool", ["get_subtitles", "get_download_link"])
def test_gated_tool_requires_get_video_info_in_session(client, info_holder, tool):
    result = call(client, tool, url=VID, user_confirmed=True)
    assert result["isError"] is True and text_of(result).endswith(": " + NOT_LOOKED_UP)
    # A lookup of another video does not open this one.
    other_id = "abcdefghijk"
    info_holder["info"] = make_info()
    info_holder["info"].video_id = other_id
    assert call(client, "get_video_info", url=other_id)["isError"] is False
    info_holder["info"] = make_info()
    assert text_of(call(client, tool, url=VID, user_confirmed=True)).endswith(": " + NOT_LOOKED_UP)
    assert info_holder["fetched"] == []


@pytest.mark.parametrize("tool", ["get_subtitles", "get_download_link"])
def test_gated_tool_runs_after_get_video_info_and_confirmation(client, info_holder, tool):
    assert call(client, "get_video_info", url=f"https://youtu.be/{VID}")["isError"] is False
    # Any URL form of the same video passes.
    result = call(client, tool, url=f"https://www.youtube.com/watch?v={VID}", user_confirmed=True)
    assert result["isError"] is False
    assert info_holder["fetched"] == ([("en", False)] if tool == "get_subtitles" else [])


@pytest.mark.parametrize("tool", ["get_subtitles", "get_download_link"])
def test_gate_is_per_session(client, tool):
    assert call(client, "get_video_info", url=VID)["isError"] is False
    assert call(client, tool, url=VID, user_confirmed=True)["isError"] is False
    second = open_session(client, APPS_CAPABILITY)
    result = call(client, tool, headers=second, url=VID, user_confirmed=True)
    assert result["isError"] is True and text_of(result).endswith(": " + NOT_LOOKED_UP)
    assert call(client, "get_video_info", headers=second, url=VID)["isError"] is False
    assert call(client, tool, headers=second, url=VID, user_confirmed=True)["isError"] is False


def test_gate_entry_dropped_when_session_ends(client):
    from app.mcp_server import _looked_up

    session = open_session(client)
    key = "http:" + session["Mcp-Session-Id"]
    assert call(client, "get_video_info", headers=session, url=VID)["isError"] is False
    assert list(_looked_up[key]) == [VID]
    assert client.delete("/mcp", headers={**HEADERS, **session}).status_code == 200
    assert key not in _looked_up


def test_gate_does_not_affect_resources(client):
    [contents] = read_resource(client, f"subtitles://video/{VID}/en/false/txt/paragraphs")["result"]["contents"]
    assert contents["text"].startswith("Title: Never Gonna Give You Up\n")
