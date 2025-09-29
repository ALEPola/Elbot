import asyncio
import os

import pytest

os.environ["MAFIC_LIBRARY"] = "nextcord"

from elbot.music.audio_backend import TrackHandle, TrackLoadFailure
from elbot.music.fallback import FallbackPlayer
from elbot.music.metrics import PlaybackMetrics
from elbot.music.cookies import CookieManager


class DummyTrack:
    def __init__(self, title: str, duration: int = 60_000) -> None:
        self.info = {
            "title": title,
            "author": "Tester",
            "length": duration,
            "uri": f"https://example.com/{title}",
            "sourceName": "youtube",
        }


def make_handle(title: str) -> TrackHandle:
    return TrackHandle.from_mafic(DummyTrack(title))


class DummyBackend:
    def __init__(self):
        self.responses = {}
        self.calls = []

    async def resolve_tracks(self, query: str, *, prefer_search: bool = True):
        self.calls.append((query, prefer_search))
        response = self.responses.get(query)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            value = response()
            if isinstance(value, Exception):
                raise value
            return value
        return response


@pytest.mark.asyncio
async def test_fallback_player_prefers_lavalink(monkeypatch):
    backend = DummyBackend()
    backend.responses["test"] = [make_handle("test")]
    player = FallbackPlayer(backend, cookies=CookieManager(), metrics=PlaybackMetrics())
    entry = await player.build_queue_entry(
        "test", requested_by=1, requester_display="tester", channel_id=123
    )
    assert not entry.is_fallback
    assert backend.calls[0][0] == "test"
    assert player.metrics.snapshot()["last_fallback_source"] is None


@pytest.mark.asyncio
async def test_fallback_player_uses_yt_dlp(monkeypatch):
    backend = DummyBackend()

    error = TrackLoadFailure("failure", cause=Exception("429"))

    def failing():
        return error

    backend.responses["broken"] = failing
    backend.responses["https://stream"] = [make_handle("fallback")]

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", lambda *_args, **_kwargs: fake_sleep(0))

    class DummyYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, query, download=False):
            assert "skip_download" in self.opts
            yt_args = self.opts.get("extractor_args", {}).get("youtube", {})
            assert "android" in yt_args.get("player_client", [])
            return {"url": "https://stream", "title": "fallback"}

    monkeypatch.setattr("yt_dlp.YoutubeDL", DummyYDL)

    async def immediate(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate)

    metrics = PlaybackMetrics()
    player = FallbackPlayer(backend, cookies=CookieManager(), metrics=metrics)

    entry = await player.build_queue_entry(
        "broken", requested_by=1, requester_display="tester", channel_id=1
    )
    assert entry.is_fallback
    snapshot = metrics.snapshot()
    assert snapshot["fallback_used"] == 1
    assert snapshot["last_fallback_source"] == "https://stream"
    assert backend.calls[-1][0] == "https://stream"

