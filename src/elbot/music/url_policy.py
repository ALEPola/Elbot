"""Restrict user-supplied media locations before either playback backend runs."""

import re
from urllib.parse import urlsplit


MEDIA_HOSTS = ("youtube.com", "youtu.be", "soundcloud.com", "spotify.com", "bandcamp.com", "vimeo.com")
SEARCH_PREFIX = re.compile(r"^(?:ytsearch\d*|ytdsearch|spsearch|scsearch):", re.I)


def validate_media_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("Provide a song query of 1–500 characters.")
    text = query.strip()
    if any(ord(c) < 32 for c in text) or "\\" in text:
        raise ValueError("Invalid characters in the song query.")
    if SEARCH_PREFIX.match(text):
        return text
    if re.match(r"^[a-z][a-z0-9+.-]*:", text, re.I) or text.startswith("//"):
        try:
            url = urlsplit(text)
            host = (url.hostname or "").lower().rstrip(".")
            valid = (
                url.scheme == "https" and not url.username and not url.password
                and url.port in (None, 443)
                and any(host == allowed or host.endswith("." + allowed) for allowed in MEDIA_HOSTS)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use a song title or an HTTPS link from YouTube, SoundCloud, Spotify, Bandcamp, or Vimeo.")
    return text
