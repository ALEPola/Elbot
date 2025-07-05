import logging
from cogs import music as music_cog

def test_get_cookiefile_path_returns_path(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("dummy")
    monkeypatch.setenv("YOUTUBE_COOKIES_PATH", str(cookie_file))
    assert music_cog.get_cookiefile_path() == str(cookie_file)

def test_get_cookiefile_path_missing(monkeypatch, caplog):
    monkeypatch.setenv("YOUTUBE_COOKIES_PATH", "/does/not/exist")
    with caplog.at_level(logging.WARNING):
        result = music_cog.get_cookiefile_path()
    assert result is None
    assert any("not found" in rec.message for rec in caplog.records)
