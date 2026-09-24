import logging
import os
import stat
import tempfile

import pytest

from app import youtube

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tsecret-value\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tEMPTY\t\n"
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("YTDLP_COOKIES", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_CONTENT", raising=False)
    youtube._reset_cookie_cache()
    yield
    path = youtube._cookie_path
    youtube._reset_cookie_cache()
    if path and os.path.isfile(path):
        os.remove(path)


def test_content_env_writes_private_file(monkeypatch, caplog):
    monkeypatch.setenv("YTDLP_COOKIES_CONTENT", COOKIES)
    with caplog.at_level(logging.DEBUG, logger="app.youtube"):
        path = youtube._ydl_options()["cookiefile"]
    assert os.path.basename(path).startswith("yt-cookies-") and path.endswith(".txt")
    assert os.path.dirname(path) == tempfile.gettempdir()
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == COOKIES  # tabs, including an empty trailing value, survive
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert youtube._ydl_options()["cookiefile"] == path  # written once, then cached
    assert "secret-value" not in caplog.text and not caplog.records


def test_path_env_wins_over_content(monkeypatch, tmp_path):
    writable = tmp_path / "cookies.txt"
    writable.write_text(COOKIES, encoding="utf-8")
    monkeypatch.setenv("YTDLP_COOKIES", str(writable))
    monkeypatch.setenv("YTDLP_COOKIES_CONTENT", "# Netscape HTTP Cookie File\nother\n")
    assert youtube._ydl_options()["cookiefile"] == str(writable)
    assert youtube._cookie_path is None


def test_read_only_path_is_copied_to_writable_temp_file(monkeypatch, tmp_path):
    source = tmp_path / "cookies.txt"
    source.write_text(COOKIES, encoding="utf-8")
    source.chmod(0o400)
    monkeypatch.setenv("YTDLP_COOKIES", str(source))
    # Pretend the source is read-only even when running as root (root can write anything).
    monkeypatch.setattr(youtube.os, "access", lambda p, *_a, **_k: str(p) != str(source))
    path = youtube._ydl_options()["cookiefile"]
    assert path != str(source)
    with open(path, "a", encoding="utf-8"):
        pass  # the copy is writable
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == COOKIES
    assert youtube._ydl_options()["cookiefile"] == path  # cached


def test_missing_path_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("YTDLP_COOKIES", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(youtube.os, "access", lambda *_a, **_k: False)
    assert "cookiefile" not in youtube._ydl_options()


def test_flattened_newlines_are_restored(monkeypatch):
    monkeypatch.setenv("YTDLP_COOKIES_CONTENT", COOKIES.replace("\n", "\\n"))
    with open(youtube._ydl_options()["cookiefile"], encoding="utf-8") as fh:
        assert fh.read() == COOKIES


@pytest.mark.parametrize("value", ["", "   ", "\n\t\n"])
def test_blank_content_ignored(monkeypatch, value):
    monkeypatch.setenv("YTDLP_COOKIES_CONTENT", value)
    assert "cookiefile" not in youtube._ydl_options()
    assert youtube._cookie_path is None


def test_non_netscape_content_warns_without_leaking(monkeypatch, caplog):
    monkeypatch.setenv("YTDLP_COOKIES_CONTENT", "SID=secret-value")
    with caplog.at_level(logging.WARNING, logger="app.youtube"):
        path = youtube._ydl_options()["cookiefile"]
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "SID=secret-value\n"
    assert "Netscape" in caplog.text and "secret-value" not in caplog.text
