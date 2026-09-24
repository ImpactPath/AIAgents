"""MCP endpoint tests: raw JSON-RPC over streamable HTTP (stateless, JSON responses), yt-dlp mocked."""

import pytest
from fastapi.testclient import TestClient

from app import youtube
from app.main import app
from app.youtube import Track, VideoInfo

VID = "dQw4w9WgXcQ"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


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
    return holder


@pytest.fixture
def client(info_holder):
    with TestClient(app) as c:
        yield c


def rpc(client, method, params=None, *, id=1, path="/mcp", headers=None, **kwargs):
    payload = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post(path, json=payload, headers={**HEADERS, **(headers or {})}, **kwargs)


def call(client, name, **arguments):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def text_of(result):
    return result["content"][0]["text"]


def test_initialize_and_tools_list(client):
    r = rpc(client, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
    })
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "youtube-subtitles"
    assert "get_video_info first" in result["instructions"]
    note = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=HEADERS)
    assert note.status_code == 202

    tools = {t["name"]: t for t in rpc(client, "tools/list", id=2).json()["result"]["tools"]}
    assert set(tools) == {"get_video_info", "get_subtitles"}
    info_tool, subs_tool = tools["get_video_info"], tools["get_subtitles"]
    assert info_tool["inputSchema"]["required"] == ["url"]
    assert {"tracks", "recommended", "original_language", "published"} <= set(info_tool["outputSchema"]["properties"])
    props = subs_tool["inputSchema"]["properties"]
    assert subs_tool["inputSchema"]["required"] == ["url"]
    assert props["fmt"]["enum"] == ["txt", "srt", "vtt"] and props["fmt"]["default"] == "txt"
    assert props["layout"]["enum"] == ["paragraphs", "sentences", "cues"]
    assert props["max_chars"]["default"] == 200000 and props["include_header"]["default"] is True
    assert "rate-limited" in subs_tool["description"] and not subs_tool["description"].startswith(" ")


def test_mcp_path_without_and_with_trailing_slash(client):
    for path in ("/mcp", "/mcp/"):
        r = rpc(client, "tools/list", path=path, follow_redirects=False)
        assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 2


def test_get_video_info_filters_and_orders(client):
    result = call(client, "get_video_info", url=f"https://youtu.be/{VID}")
    assert result["isError"] is False
    data = result["structuredContent"]
    assert [(t["lang"], t["auto"]) for t in data["tracks"]] == [
        ("en", False), ("ko", False), ("de", False), ("fr", False), ("en-orig", True), ("es-orig", True),
    ]
    assert data["recommended"] == {"lang": "en", "auto": False}
    assert data["duration"] == "03:32" and data["duration_seconds"] == 212
    assert data["published"] == "2009-10-25" and data["original_language"] == "en"
    assert data["url"] == f"https://www.youtube.com/watch?v={VID}"


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
    result = call(client, "get_subtitles", url=VID)
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
    result = call(client, "get_subtitles", url=VID, **args)
    assert result["isError"] is False
    assert info_holder["fetched"] == [expected]


def test_get_subtitles_formats_and_layouts(client, info_holder):
    info_holder["cues"] = 3
    srt = text_of(call(client, "get_subtitles", url=VID, fmt="srt"))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:00,001\nTitle: ") and "\n\n2\n00:00:00,000 --> " in srt
    vtt = text_of(call(client, "get_subtitles", url=VID, fmt="vtt", include_header=False))
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> ")
    sentences = text_of(call(client, "get_subtitles", url=VID, layout="sentences", include_header=False))
    assert sentences == "Line 0 from en manual.\nLine 1 from en manual.\nLine 2 from en manual.\n"
    paragraphs = text_of(call(client, "get_subtitles", url=VID, include_header=False))
    assert paragraphs == "Line 0 from en manual. Line 1 from en manual. Line 2 from en manual.\n"
    bad = call(client, "get_subtitles", url=VID, fmt="docx")
    assert bad["isError"] is True


def test_get_subtitles_truncates_at_line_boundary(client, info_holder):
    info_holder["cues"] = 40
    full = text_of(call(client, "get_subtitles", url=VID, layout="sentences"))
    text = text_of(call(client, "get_subtitles", url=VID, layout="sentences", max_chars=300))
    kept, _, marker = text.rpartition("\n")
    assert len(kept) <= 300 and full.startswith(kept + "\n")
    assert marker == f"[truncated: {len(full) - len(kept)} more characters]"
    assert text_of(call(client, "get_subtitles", url=VID, max_chars=10**6)) == text_of(call(client, "get_subtitles", url=VID))


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
    result = call(client, tool, **args)
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
    result = call(client, "get_subtitles", url=VID, lang="ko", auto=True)
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
        assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 2, kwargs
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
            assert rpc(c, "tools/list").status_code == 200
