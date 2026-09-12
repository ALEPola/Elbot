"""Phase 5/6 DJ tools: read-only lookups must never mutate state, and the
Phase 6 playback-mutating tools must behave the same way the equivalent
slash command / control button does.
"""

import asyncio
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


class FakeMutableQueue(FakeQueue):
    def add(self, entry):
        self._tracks.append(entry)

    def add_next(self, entry):
        self._tracks.insert(0, entry)

    def __len__(self):
        return len(self._tracks)


class FakeFallback:
    def __init__(self, entry=None, error=None):
        self.entry = entry
        self.error = error
        self.calls = []

    async def build_queue_entry(self, query, *, requested_by, requester_display, channel_id):
        self.calls.append(
            {
                "query": query,
                "requested_by": requested_by,
                "requester_display": requester_display,
                "channel_id": channel_id,
            }
        )
        if self.error:
            raise self.error
        return self.entry


def make_mutable_music(
    now_playing=None, player=None, fallback_entry=None, fallback_error=None, controller=None
):
    state = SimpleNamespace(
        now_playing=now_playing,
        player=player,
        queue=FakeMutableQueue([]),
        lock=asyncio.Lock(),
        last_channel_id=42,
    )
    calls = {"ensure_playing": 0, "refresh": 0}

    async def ensure_playing(_guild_id):
        calls["ensure_playing"] += 1

    async def refresh_now_playing(_guild_id):
        calls["refresh"] += 1

    async def skip_current(_guild_id, _state):
        return True, "Skipped the current track."

    fallback = FakeFallback(entry=fallback_entry, error=fallback_error)
    music = SimpleNamespace(
        _states={1: state},
        _get_state=lambda gid: state,
        _ensure_playing=ensure_playing,
        _refresh_now_playing=refresh_now_playing,
        _skip_current=skip_current,
        backend=SimpleNamespace(),
        fallback=fallback,
        bot=SimpleNamespace(user=SimpleNamespace(id=999)),
    )
    guild = SimpleNamespace(id=1)
    tools = build_tools(music, guild, controller)
    return tools, state, fallback, calls


@pytest.mark.asyncio
async def test_play_track_queues_and_attributes_the_active_speaker():
    entry = FakeTrack("New Song", "New Artist", 120_000, "unused")
    controller = SimpleNamespace(active_speaker=555, active_name="Nave")
    tools, state, fallback, calls = make_mutable_music(
        player=SimpleNamespace(), fallback_entry=entry, controller=controller
    )

    result = await tools["play_track"]({"query": "new song"})

    assert result == {
        "queued": True,
        "title": "New Song",
        "author": "New Artist",
        "queue_position": 1,
    }
    assert fallback.calls == [
        {"query": "new song", "requested_by": 555, "requester_display": "Nave", "channel_id": 42}
    ]
    assert state.queue.snapshot() == [entry]
    assert calls == {"ensure_playing": 1, "refresh": 1}


@pytest.mark.asyncio
async def test_play_track_next_inserts_at_front():
    existing = FakeTrack("Old Song", "Old Artist", 1000, "Nave")
    entry = FakeTrack("New Song", "New Artist", 1000, "unused")
    tools, state, _fallback, _calls = make_mutable_music(player=SimpleNamespace(), fallback_entry=entry)
    state.queue.add(existing)

    result = await tools["play_track"]({"query": "new song", "play_next": True})

    assert result["queue_position"] == 1
    assert state.queue.snapshot()[0] is entry


@pytest.mark.asyncio
async def test_play_track_requires_a_voice_connection():
    tools, _state, fallback, _calls = make_mutable_music(player=None)
    result = await tools["play_track"]({"query": "song"})
    assert result == {"error": "not connected to a voice channel"}
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_play_track_empty_query_short_circuits():
    tools, _state, fallback, _calls = make_mutable_music(player=SimpleNamespace())
    assert await tools["play_track"]({"query": "  "}) == {"error": "no query given"}
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_play_track_surfaces_resolution_errors_without_raising():
    tools, _state, _fallback, calls = make_mutable_music(
        player=SimpleNamespace(), fallback_error=RuntimeError("no results")
    )
    result = await tools["play_track"]({"query": "song"})
    assert "no results" in result["error"]
    assert calls == {"ensure_playing": 0, "refresh": 0}


@pytest.mark.asyncio
async def test_play_track_falls_back_to_bot_identity_without_a_controller():
    entry = FakeTrack("Song", "Artist", 1000, "unused")
    tools, _state, fallback, _calls = make_mutable_music(player=SimpleNamespace(), fallback_entry=entry)
    await tools["play_track"]({"query": "song"})
    assert fallback.calls[0]["requested_by"] == 999
    assert fallback.calls[0]["requester_display"] == "voice request"


@pytest.mark.asyncio
async def test_skip_track_delegates_to_skip_current():
    tools, _state, _fallback, _calls = make_mutable_music(player=SimpleNamespace())
    assert await tools["skip_track"]({}) == {"skipped": True, "message": "Skipped the current track."}


@pytest.mark.asyncio
async def test_skip_track_with_no_guild_state():
    music = SimpleNamespace(_states={})
    guild = SimpleNamespace(id=1)
    tools = build_tools(music, guild)
    result = await tools["skip_track"]({})
    assert result == {"skipped": False, "message": "Nothing is playing right now."}


@pytest.mark.asyncio
async def test_pause_and_resume_toggle_the_player():
    player = SimpleNamespace(paused=False)

    async def pause():
        player.paused = True

    async def resume():
        player.paused = False

    player.pause = pause
    player.resume = resume
    tools, _state, _fallback, calls = make_mutable_music(player=player)

    assert await tools["pause_playback"]({}) == {"paused": True, "message": "Playback paused."}
    assert player.paused is True
    assert await tools["pause_playback"]({}) == {"paused": True, "message": "Already paused."}

    assert await tools["resume_playback"]({}) == {"paused": False, "message": "Playback resumed."}
    assert player.paused is False
    assert await tools["resume_playback"]({}) == {"paused": False, "message": "Already playing."}
    assert calls["refresh"] == 2  # only the two state-changing calls refresh the embed


@pytest.mark.asyncio
async def test_pause_without_a_player_reports_nothing_playing():
    tools, _state, _fallback, _calls = make_mutable_music(player=None)
    assert await tools["pause_playback"]({}) == {"paused": False, "message": "Nothing is playing right now."}


@pytest.mark.asyncio
async def test_set_volume_clamps_to_valid_range():
    player = SimpleNamespace(set_volume=None)
    levels = []

    async def set_volume(level):
        levels.append(level)

    player.set_volume = set_volume
    tools, state, _fallback, calls = make_mutable_music(player=player)

    assert await tools["set_volume"]({"level": 500}) == {"volume": 200}
    assert await tools["set_volume"]({"level": -10}) == {"volume": 0}
    assert await tools["set_volume"]({"level": 80}) == {"volume": 80}
    assert levels == [200, 0, 80]
    assert state.volume == 80
    assert calls["refresh"] == 3


@pytest.mark.asyncio
async def test_set_volume_rejects_non_numeric_level():
    tools, _state, _fallback, _calls = make_mutable_music(player=SimpleNamespace())
    result = await tools["set_volume"]({"level": "loud"})
    assert result == {"error": "level must be an integer"}
