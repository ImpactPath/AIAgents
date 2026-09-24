from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import youtube
from app.main import app, safe_title
from app.youtube import Track, VideoInfo, normalize_info, parse_video_id

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"
SAMPLE_VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000 align:start\n<c>Hello</c> there\n\n00:00:02.000 --> 00:00:03.000\nGeneral Kenobi\n"


@pytest.mark.parametrize(
    "text",
    [
        VID,
        f"  {VID}  ",
        URL,
        f"https://youtube.com/watch?feature=share&v={VID}&t=42s",
        f"http://m.youtube.com/watch?v={VID}",
        f"https://music.youtube.com/watch?v={VID}&list=RDAMVM",
        f"https://youtu.be/{VID}?si=abc123",
        f"youtu.be/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
        f"https://www.youtube.com/embed/{VID}",
        f"www.youtube.com/watch?v={VID}#t=10",
        f"https://www.youtube.com/watch?v={VID})",
    ],
)
def test_parse_video_id_accepts(text):
    assert parse_video_id(text) == VID


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "not-a-url",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/channel/UC1234567890",
        "ftp://youtu.be/dQw4w9WgXcQ",
        "https://evil.com/?u=https://youtu.be/dQw4w9WgXcQ",
        "dQw4w9WgXcQextra",
    ],
)
def test_parse_video_id_rejects(text):
    assert parse_video_id(text) is None


def make_info(title="Never Gonna Give You Up", tracks=None):
    if tracks is None:
        tracks = [
            Track("en", "English", False, "u1"),
            Track("ko", "Korean", False, "u2"),
            Track("en", "English (auto-generated)", True, "u3"),
        ]
    return VideoInfo(VID, title, "Rick Astley", 212, "https://i.ytimg.com/x.jpg", tracks)


@pytest.fixture
def client(monkeypatch):
    calls = {"info": [], "sub": []}

    def fake_info(video_id):
        calls["info"].append(video_id)
        return make_info()

    def fake_sub(track):
        calls["sub"].append(track)
        return SAMPLE_VTT

    monkeypatch.setattr(youtube, "fetch_info", fake_info)
    monkeypatch.setattr(youtube, "fetch_subtitle_text", fake_sub)
    c = TestClient(app)
    c.calls = calls
    return c


def test_normalize_info_ordering_and_entry_choice():
    raw = {
        "title": "T",
        "uploader": "Up",
        "duration": 12.0,
        "subtitles": {
            "ko": [{"ext": "json3", "url": "j"}, {"ext": "vtt", "url": "kv", "name": "Korean"}],
            "en": [{"ext": "srv3", "url": "s"}, {"ext": "srt", "url": "es", "name": "English"}],
            "live_chat": [{"ext": "json", "url": "lc"}],
        },
        "automatic_captions": {
            "fr": [{"ext": "json3", "url": "j"}, {"ext": "vtt", "url": "fv", "name": "French"}],
            "de": [{"ext": "srv1", "url": "s"}],
            "en": [{"ext": "vtt", "url": "ev", "name": "English (auto-generated)"}],
        },
    }
    info = normalize_info(VID, raw)
    assert [(t.lang, t.auto, t.url) for t in info.tracks] == [
        ("en", False, "es"),
        ("ko", False, "kv"),
        ("en", True, "ev"),
        ("fr", True, "fv"),
    ]
    assert info.tracks[3].name == "French (auto-generated)"
    assert info.public()["channel"] == "Up" and info.duration == 12


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_info_ok(client):
    r = client.get("/api/info", params={"url": f"https://youtu.be/{VID}"})
    assert r.status_code == 200
    body = r.json()
    assert body["video_id"] == VID and body["duration"] == 212
    assert body["tracks"] == [
        {"lang": "en", "name": "English", "auto": False},
        {"lang": "ko", "name": "Korean", "auto": False},
        {"lang": "en", "name": "English (auto-generated)", "auto": True},
    ]
    assert client.calls["info"] == [VID]


@pytest.mark.parametrize("params", [{"url": "not-a-url"}, {"url": "https://vimeo.com/123"}, {}])
def test_info_400(client, params):
    r = client.get("/api/info", params=params)
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)
    assert client.calls["info"] == []


def test_info_404_no_tracks(client, monkeypatch):
    monkeypatch.setattr(youtube, "fetch_info", lambda vid: make_info(tracks=[]))
    r = client.get("/api/info", params={"url": URL})
    assert r.status_code == 404 and "subtitles" in r.json()["detail"]


def test_info_502(client, monkeypatch):
    def boom(vid):
        raise youtube.YoutubeError("[youtube] dQw4w9WgXcQ: Video unavailable")

    monkeypatch.setattr(youtube, "fetch_info", boom)
    r = client.get("/api/info", params={"url": URL})
    assert r.status_code == 502 and "Video unavailable" in r.json()["detail"]


def test_error_message_is_one_line_and_mentions_cookies():
    msg = youtube._one_line(Exception("ERROR: [youtube] x: Sign in to confirm you're not a bot.\nMore text"))
    assert "\n" not in msg and msg.startswith("[youtube]") and "cookies" in msg


def test_fetch_info_wraps_ytdlp_errors(monkeypatch):
    import yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            assert url == f"https://www.youtube.com/watch?v={VID}" and download is False
            raise yt_dlp.utils.DownloadError("ERROR: [youtube] boom\ntraceback junk")

    youtube.clear_cache()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(youtube.YoutubeError) as err:
        youtube.fetch_info(VID)
    assert str(err.value) == "[youtube] boom"


def test_fetch_info_is_cached(monkeypatch):
    import yt_dlp

    count = []

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            count.append(url)
            return {"title": "Cached", "subtitles": {"en": [{"ext": "vtt", "url": "u"}]}}

    youtube.clear_cache()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    assert youtube.fetch_info(VID).title == "Cached"
    assert youtube.fetch_info(VID).tracks[0].lang == "en"
    assert len(count) == 1
    youtube.clear_cache()


@pytest.mark.parametrize(
    "fmt,ctype,start",
    [
        ("srt", "application/x-subrip; charset=utf-8", "1\n00:00:01,000 --> 00:00:02,000\nHello there\n"),
        ("vtt", "text/vtt; charset=utf-8", "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there\n"),
        ("txt", "text/plain; charset=utf-8", "Hello there\nGeneral Kenobi\n"),
    ],
)
def test_download_formats(client, fmt, ctype, start):
    r = client.get("/api/download", params={"url": URL, "lang": "en", "auto": "false", "fmt": fmt})
    assert r.status_code == 200
    assert r.headers["content-type"] == ctype
    assert r.text.startswith(start)
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; ")
    assert f'filename="Never Gonna Give You Up.en.{fmt}"' in cd
    assert f"filename*=UTF-8''Never%20Gonna%20Give%20You%20Up.en.{fmt}" in cd


def test_download_defaults_and_auto_suffix(client):
    r = client.get("/api/download", params={"url": VID, "lang": "en", "auto": "1"})
    assert r.status_code == 200
    assert 'filename="Never Gonna Give You Up.en.auto.srt"' in r.headers["content-disposition"]
    assert client.calls["sub"][0].auto is True


def test_download_korean_title(client, monkeypatch):
    title = "아이유: 좋은 날 / Live?"
    monkeypatch.setattr(youtube, "fetch_info", lambda vid: make_info(title=title))
    r = client.get("/api/download", params={"url": URL, "lang": "ko", "fmt": "txt"})
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert f'filename="{VID}.ko.txt"' in cd
    assert "filename*=UTF-8''" + quote("아이유 좋은 날 Live.ko.txt", safe="") in cd
    cd.encode("latin-1")  # header must be encodable


def test_safe_title():
    assert safe_title('  a/b\\c:d*e?f"g<h>i|j  ', "x") == "a b c d e f g h i j"
    assert safe_title("x" * 200, "id") == "x" * 80
    assert safe_title("???", "fallback") == "fallback"
    assert safe_title("tab\tand\nnewline", "id") == "tabandnewline"


@pytest.mark.parametrize(
    "params,status",
    [
        ({"url": "https://example.com", "lang": "en"}, 400),
        ({"url": URL, "lang": "en", "fmt": "docx"}, 400),
        ({"url": URL, "lang": "en", "auto": "maybe"}, 400),
        ({"url": URL}, 400),
        ({"url": URL, "lang": "fr"}, 404),
        ({"url": URL, "lang": "ko", "auto": "true"}, 404),
    ],
)
def test_download_errors(client, params, status):
    r = client.get("/api/download", params=params)
    assert r.status_code == status
    assert isinstance(r.json()["detail"], str)
    assert client.calls["sub"] == []


def test_download_502_on_subtitle_fetch(client, monkeypatch):
    def boom(track):
        raise youtube.YoutubeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(youtube, "fetch_subtitle_text", boom)
    r = client.get("/api/download", params={"url": URL, "lang": "en"})
    assert r.status_code == 502 and "429" in r.json()["detail"]


def test_index_served_or_404(client):
    r = client.get("/")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("text/html")


def test_error_message_drops_ytdlp_boilerplate():
    raw = (
        "ERROR: [youtube] abc: Unable to download API page: HTTP Error 403 (caused by "
        "HTTPError('boom')); please report this issue on  https://github.com/yt-dlp/yt-dlp/issues"
    )
    assert youtube._one_line(Exception(raw)) == "[youtube] abc: Unable to download API page: HTTP Error 403"
