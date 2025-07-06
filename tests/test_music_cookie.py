from cogs import music as music_cog


def test_cookie_helpers_removed():
    assert not hasattr(music_cog, "get_cookiefile_path")
    assert not hasattr(music_cog, "load_cookie")
