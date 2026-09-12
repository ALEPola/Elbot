"""Phase 5 read-only DJ tools: must reflect real state and never mutate it."""

from types import SimpleNamespace

import pytest

from elbot.live.tools import build_tools


class FakeHandle:
    def __init__(self, title, author, duration, source="youtube"):
        self.title, self.author, self.duration, self.source = title, author, duration, source


class FakeTrack:
    def __init__(self, title, author, duration, requester_display):
        self.handle = FakeHandle(title, author, duration)
        self.requester_display = requester_display


class FakeQueue:
    def __init__(self, tracks):
        self._tracks = tracks

    def snapshot(self):
        return list(self._tracks)


class FakeBackend:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.queries = []

    async def resolve_tracks(self, query, *, prefer_search=True):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


def make_music(now_playing=None, queue_tracks=(), backend=None, playback_started_at=0.0):
    state = SimpleNamespace(
        now_playing=now_playing, queue=FakeQueue(list(queue_tracks)),
        playback_started_at=playback_started_at,
    )
    music = SimpleNamespace(_states={1: state}, backend=backend or FakeBackend())
    guild = SimpleNamespace(id=1)
    return music, guild, state


@pytest.mark.asyncio
async def test_get_current_track_reports_nothing_playing():
    music, guild, _ = make_music(now_playing=None)
    tools = build_tools(music, guild)
    assert await tools["get_current_track"]({}) == {"playing": False}


@pytest.mark.asyncio
async def test_get_current_track_reports_position_and_requester(monkeypatch):
    import time

    track = FakeTrack("Song", "Artist", duration=200_000, requester_display="Alexis")
    music, guild, _ = make_music(now_playing=track, playback_started_at=time.monotonic() - 5)
    tools = build_tools(music, guild)
    result = await tools["get_current_track"]({})
    assert result["playing"] is True
    assert result["title"] == "Song" and result["requested_by"] == "Alexis"
    assert 4500 <= result["position_ms"] <= 5500
    assert result["duration_ms"] == 200_000


@pytest.mark.asyncio
async def test_get_current_track_position_never_exceeds_duration():
    import time

    track = FakeTrack("Song", "Artist", duration=1000, requester_display="Alexis")
    music, guild, _ = make_music(now_playing=track, playback_started_at=time.monotonic() - 60)
    tools = build_tools(music, guild)
    result = await tools["get_current_track"]({})
    assert result["position_ms"] == 1000


@pytest.mark.asyncio
async def test_get_queue_respects_limit_and_reports_true_count():
    tracks = [FakeTrack(f"T{i}", "A", 1000, "Nave") for i in range(30)]
    music, guild, _ = make_music(queue_tracks=tracks)
    tools = build_tools(music, guild)
    result = await tools["get_queue"]({"limit": 3})
    assert result["count"] == 30
    assert [t["title"] for t in result["queue"]] == ["T0", "T1", "T2"]


@pytest.mark.asyncio
async def test_get_queue_default_and_max_limit():
    tracks = [FakeTrack(f"T{i}", "A", 1000, "Nave") for i in range(30)]
    music, guild, _ = make_music(queue_tracks=tracks)
    tools = build_tools(music, guild)
    assert len((await tools["get_queue"]({}))["queue"]) == 10
    assert len((await tools["get_queue"]({"limit": 999}))["queue"]) == 25
    assert len((await tools["get_queue"]({"limit": "not a number"}))["queue"]) == 10


@pytest.mark.asyncio
async def test_search_track_never_touches_the_queue():
    backend = FakeBackend(results=[FakeHandle("A", "B", 1000), FakeHandle("C", "D", 2000)])
    music, guild, state = make_music(queue_tracks=[], backend=backend)
    tools = build_tools(music, guild)
    result = await tools["search_track"]({"query": "some song"})
    assert backend.queries == ["some song"]
    assert [r["title"] for r in result["results"]] == ["A", "C"]
    assert state.queue.snapshot() == []  # unchanged: read-only


@pytest.mark.asyncio
async def test_search_track_empty_query_short_circuits():
    backend = FakeBackend(results=[FakeHandle("A", "B", 1000)])
    music, guild, _ = make_music(backend=backend)
    tools = build_tools(music, guild)
    assert await tools["search_track"]({"query": "  "}) == {"results": []}
    assert backend.queries == []  # never called the backend


@pytest.mark.asyncio
async def test_search_track_surfaces_backend_errors_without_raising():
    backend = FakeBackend(error=RuntimeError("lavalink down"))
    music, guild, _ = make_music(backend=backend)
    tools = build_tools(music, guild)
    result = await tools["search_track"]({"query": "x"})
    assert "lavalink down" in result["error"]


@pytest.mark.asyncio
async def test_get_requester_matches_current_track_or_is_null():
    music, guild, _ = make_music(now_playing=None)
    tools = build_tools(music, guild)
    assert await tools["get_requester"]({}) == {"requested_by": None}

    track = FakeTrack("Song", "Artist", 1000, "PURPLExACE1345")
    music, guild, _ = make_music(now_playing=track)
    tools = build_tools(music, guild)
    assert await tools["get_requester"]({}) == {"requested_by": "PURPLExACE1345"}


@pytest.mark.asyncio
async def test_recommend_similar_excludes_the_current_track_and_caps_at_three():
    current = FakeTrack("Same Song", "Artist", 1000, "Nave")
    backend = FakeBackend(results=[
        FakeHandle("Same Song", "Artist", 1000),  # must be excluded
        FakeHandle("B", "Artist", 1000), FakeHandle("C", "Artist", 1000),
        FakeHandle("D", "Artist", 1000), FakeHandle("E", "Artist", 1000),
    ])
    music, guild, _ = make_music(now_playing=current, backend=backend)
    tools = build_tools(music, guild)
    result = await tools["recommend_similar"]({})
    assert backend.queries == ["Artist mix"]
    titles = [r["title"] for r in result["results"]]
    assert "Same Song" not in titles
    assert len(titles) == 3


@pytest.mark.asyncio
async def test_recommend_similar_with_nothing_playing():
    music, guild, _ = make_music(now_playing=None)
    tools = build_tools(music, guild)
    assert await tools["recommend_similar"]({}) == {"results": []}
