import pytest

from elbot.music.url_policy import validate_media_query


@pytest.mark.parametrize("value", [
    "https://127.0.0.1:8000/", "http://192.168.1.5/", "https://[::1]/",
    "https://169.254.169.254/", "file:///etc/passwd", "ftp://example.com/song",
    "https://youtube.com.attacker.test/watch", "https://youtube.com@127.0.0.1/",
    "https://youtube.com:8000/", "//127.0.0.1/", "https://youtube.com\\@127.0.0.1/",
    "https://youtube.com/\nwatch", "https://youtube.com:invalid/",
])
def test_private_and_unsupported_urls_are_rejected(value):
    with pytest.raises(ValueError):
        validate_media_query(value)


@pytest.mark.parametrize("value", ["song title", "ytsearch:artist song", "ytsearch5:artist", "scsearch:track",
                                   "https://youtu.be/abc", "https://www.youtube.com/watch?v=abc",
                                   "https://artist.bandcamp.com/track/song"])
def test_supported_sources_and_searches_are_accepted(value):
    assert validate_media_query(value) == value
