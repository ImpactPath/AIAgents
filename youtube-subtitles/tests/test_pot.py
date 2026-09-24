"""PO token handling: warning capture, the withheld-subtitles message, and the provider ping."""

import pytest
from fastapi.testclient import TestClient

from app import youtube
from app.main import app

VID = "dQw4w9WgXcQ"
POT_WARNING = f"[youtube] {VID}: There are missing subtitles languages because a PO token was not provided."


def _fake_ydl(info: dict, warning: str | None):
    class FakeYDL:
        def __init__(self, opts):
            self.logger = opts["logger"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            if warning:
                self.logger.warning(warning)
            return info

    return FakeYDL


@pytest.fixture(autouse=True)
def _clear_cache():
    youtube.clear_cache()
    yield
    youtube.clear_cache()


def test_logger_collects_warnings(caplog):
    logger = youtube._QuietLogger()
    with caplog.at_level("WARNING"):
        logger.warning("something \x1b[0;33mcolored\x1b[0m")
    assert logger.warnings == ["something \x1b[0;33mcolored\x1b[0m"]
    assert "yt-dlp: something colored" in caplog.text


def test_warnings_mention_po_token():
    assert youtube.warnings_mention_po_token([POT_WARNING])
    assert youtube.warnings_mention_po_token(["Some web client subtitles require a PO Token which was not provided."])
    assert not youtube.warnings_mention_po_token(["unrelated"])
    assert not youtube.warnings_mention_po_token([])


def test_fetch_info_flags_withheld_subtitles_and_skips_cache(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _fake_ydl({"title": "t", "subtitles": {}, "automatic_captions": {}}, POT_WARNING))
    info = youtube.fetch_info(VID)
    assert info.tracks == [] and info.pot_blocked is True
    assert youtube._cache_get(VID) is None  # a recovered provider is picked up next time
    assert youtube.no_subtitles_message(info) == youtube.NO_SUBTITLES_POT


def test_fetch_info_without_warning_keeps_plain_message(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _fake_ydl({"title": "t", "subtitles": {}, "automatic_captions": {}}, None))
    info = youtube.fetch_info(VID)
    assert info.pot_blocked is False
    assert youtube.no_subtitles_message(info) == youtube.NO_SUBTITLES
    assert youtube._cache_get(VID) is info


def test_api_info_reports_po_token_problem(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _fake_ydl({"title": "t", "subtitles": {}, "automatic_captions": {}}, POT_WARNING))
    with TestClient(app) as client:
        res = client.get("/api/info", params={"url": VID})
    assert res.status_code == 404
    assert res.json()["detail"] == youtube.NO_SUBTITLES_POT


def test_pot_provider_status_unreachable(monkeypatch):
    monkeypatch.setattr(youtube, "POT_PROVIDER_URL", "http://127.0.0.1:9")  # nothing listens on port 9
    assert youtube.pot_provider_status(timeout=0.2) == {"reachable": False, "version": None}


def test_healthz_reports_provider(monkeypatch):
    monkeypatch.setattr(youtube, "pot_provider_status", lambda timeout=1.0: {"reachable": True, "version": "2.0.0"})
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok", "pot_provider": {"reachable": True, "version": "2.0.0"}}
    monkeypatch.setattr(youtube, "pot_provider_status", lambda timeout=1.0: {"reachable": False, "version": None})
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["status"] == "ok" and body["pot_provider"]["reachable"] is False


@pytest.mark.parametrize(
    "info, expected",
    [
        # dubbed video: many -orig keys, formats say the original audio is English
        ({"formats": [{"language": "ar", "language_preference": -1}, {"language": "en", "language_preference": 10},
                      {"language": "ko", "language_preference": -1}],
          "automatic_captions": {"ar-orig": [], "en-orig": [], "ko-orig": []}}, "en"),
        # default audio (5) counts when no original (10) is marked
        ({"formats": [{"language": "ja", "language_preference": 5}], "automatic_captions": {"ar-orig": []}}, "ja"),
        # formats without a preference fall back to the language field
        ({"formats": [{"language": "de", "language_preference": -1}], "language": "fr"}, "fr"),
        # no signals: several -orig keys prefer English
        ({"automatic_captions": {"ar-orig": [], "en-US-orig": [], "ko-orig": []}}, "en-US"),
        # single -orig key
        ({"automatic_captions": {"ko-orig": [], "ko": []}}, "ko"),
        ({}, None),
    ],
)
def test_original_language_prefers_original_audio_track(info, expected):
    assert youtube.original_language(info) == expected


def test_dubbed_video_orders_original_and_korean_first():
    info = youtube.normalize_info(VID, {
        "title": "t",
        "formats": [{"language": "en", "language_preference": 10}],
        "automatic_captions": {k: [{"ext": "vtt", "url": "u", "name": k}] for k in ("ar-orig", "bn-orig", "en-orig", "ko-orig")},
    })
    assert info.original_language == "en"
    langs = [t.lang for t in youtube.downloadable_tracks(info)]
    assert langs[:2] == ["en-orig", "ko-orig"] and set(langs) == {"ar-orig", "bn-orig", "en-orig", "ko-orig"}
    assert youtube.recommended_track(info).lang == "en-orig"
