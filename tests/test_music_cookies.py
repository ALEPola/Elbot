import os
import time

os.environ["MAFIC_LIBRARY"] = "nextcord"

from elbot.music.cookies import CookieManager


def test_cookie_manager_tracks_mtime(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape cookies\n")

    os.environ["YT_COOKIES_FILE"] = str(cookie_file)
    manager = CookieManager()
    assert manager.cookie_file() is not None

    manager.cookie_age_seconds()
    time.sleep(0.01)
    cookie_file.write_text("# Updated\n")
    age2 = manager.cookie_age_seconds()
    assert age2 is not None
    assert age2 is None or age2 >= 0
    opts = manager.yt_dlp_options()
    assert opts["cookiefile"] == str(cookie_file)
    yt_args = opts.get("extractor_args", {}).get("youtube", {})
    assert "android" in yt_args.get("player_client", [])
    assert "webpage" in yt_args.get("player_skip", [])


def test_cookie_manager_handles_missing_file(tmp_path):
    missing = tmp_path / "missing.txt"
    os.environ["YT_COOKIES_FILE"] = str(missing)
    manager = CookieManager()
    assert manager.cookie_file() == missing
    assert manager.cookie_age_seconds() is None
    opts = manager.yt_dlp_options()
    assert "cookiefile" not in opts
    yt_args = opts.get("extractor_args", {}).get("youtube", {})
    assert "android" in yt_args.get("player_client", [])

