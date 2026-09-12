import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from elbot.cogs.music import GuildState, Music, MusicControls
from elbot.music import EmbedFactory, QueuedTrack, TrackHandle
from elbot.music.support import _format_duration, _progress_bar


class DummyTrack:
    def __init__(self, title: str, identifier: str) -> None:
        self.info = {
            "title": title,
            "author": "Test Artist",
            "length": 163_000,
            "uri": f"https://example.test/{identifier}",
            "sourceName": "youtube",
            "identifier": identifier,
        }


def make_entry(title: str = "Test Song", identifier: str = "abc123") -> QueuedTrack:
    return QueuedTrack(
        id=identifier,
        handle=TrackHandle.from_mafic(DummyTrack(title, identifier)),
        query=title,
        channel_id=10,
        requested_by=42,
        requester_display="listener",
    )


def test_now_playing_embed_matches_controller_state():
    entry = make_entry()

    embed = EmbedFactory().now_playing(
        entry,
        position=61_000,
        queue_size=3,
        volume=85,
        loop_mode="queue",
        voice_channel="#music",
        paused=True,
        autoplay=True,
        likes=2,
    )

    assert embed.title == "Now playing"
    assert "<@42>" in embed.description
    assert "#music" in embed.description
    assert "Queue: `3`" in embed.fields[0].value
    assert "Volume: `85%`" in embed.fields[0].value
    assert embed.fields[1].name == "⏸ Paused"
    assert "`1:01`" in embed.fields[1].value
    assert "`2:43`" in embed.fields[1].value
    assert embed.thumbnail.url.endswith("/abc123/hqdefault.jpg")
    assert "AutoPlay on" in embed.footer.text
    assert "2 likes" in embed.footer.text


def test_duration_and_progress_formatting_are_compact_and_clamped():
    assert _format_duration(0) == "0:00"
    assert _format_duration(163_000) == "2:43"
    assert _format_duration(3_723_000) == "1:02:03"
    assert _progress_bar(-1, 100, width=4) == "●────"
    assert _progress_bar(100, 100, width=4) == "━━━━●"


@pytest.mark.asyncio
async def test_music_controls_reflect_pause_and_autoplay(monkeypatch):
    monkeypatch.delenv("ELBOT_MUSIC_DASHBOARD_URL", raising=False)
    state = GuildState(player=SimpleNamespace(paused=True), autoplay=True)
    cog = SimpleNamespace(_states={7: state})

    view = MusicControls(cog, 7)

    assert view.pause_button.label == "Resume"
    assert view.autoplay_button.label == "AutoPlay: On"
    assert view.autoplay_button.style.name == "success"
    assert [item.label for item in view.children[:5]] == [
        "Resume",
        "Skip",
        "Stop",
        "AutoPlay: On",
        "Queue",
    ]
    view.stop()


def test_feedback_vote_can_be_changed():
    music = Music.__new__(Music)
    music._track_feedback = {}
    entry = make_entry()

    assert music._record_feedback(entry, 100, loved=True) == (1, 0)
    assert music._record_feedback(entry, 100, loved=False) == (0, 1)


@pytest.mark.asyncio
async def test_autoplay_skips_recent_tracks():
    previous = make_entry("Previous", "previous")
    repeated = make_entry("Previous", "repeated").handle
    fresh = make_entry("Something New", "fresh").handle
    backend = SimpleNamespace(
        resolve_tracks=AsyncMock(return_value=[repeated, fresh]),
    )
    music = Music.__new__(Music)
    music._backend = backend
    music.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    music.logger = logging.getLogger("test.music.controller")
    state = GuildState(autoplay=True)
    state.autoplay_history.append(music._track_identity(previous))

    added = await music._enqueue_autoplay_track(7, state, previous)

    assert added
    queued = state.queue.peek()
    assert queued is not None
    assert queued.handle.title == "Something New"
    assert queued.requester_display == "AutoPlay"


@pytest.mark.asyncio
async def test_playback_tracks_lavalinks_canonical_http_copy():
    entry = make_entry("Direct Audio", "resolved-http-track")
    canonical = DummyTrack("Direct Audio", "canonical-playing-track")
    player = SimpleNamespace(
        current=canonical,
        play=AsyncMock(),
    )
    state = GuildState(player=player)
    state.queue.add(entry)

    music = Music.__new__(Music)
    music._states = {7: state}
    music.logger = logging.getLogger("test.music.controller")
    music.metrics = SimpleNamespace(
        incr_started=Mock(),
        incr_failed=Mock(),
    )
    music._wait_for_player_connection = AsyncMock(return_value=True)
    music._announce_now_playing = AsyncMock()
    music._resolve_mafic = lambda: SimpleNamespace(PlayerNotConnected=RuntimeError)

    await music._begin_playback(7)

    assert state.now_playing is entry
    assert state.now_playing.handle.track is canonical
    assert music._track_key(state.now_playing.handle.track) == music._track_key(canonical)


@pytest.mark.asyncio
async def test_track_start_captures_lavalinks_canonical_track():
    """Regression: player.current can stay stale/None right after play()
    for HTTP/fallback sources (the REST update response's "track" field is
    not guaranteed), so _begin_playback's reassignment can silently no-op.
    TrackStartEvent's own track is authoritative and must win.
    """
    entry = make_entry("Direct Audio", "resolved-http-track")
    state = GuildState()
    state.now_playing = entry

    music = Music.__new__(Music)
    music._states = {7: state}

    canonical = DummyTrack("Direct Audio", "canonical-playing-track")
    event = SimpleNamespace(player=SimpleNamespace(guild=SimpleNamespace(id=7)), track=canonical)

    await music.on_track_start(event)

    assert state.now_playing.handle.track is canonical
    assert music._track_key(state.now_playing.handle.track) == music._track_key(canonical)


@pytest.mark.asyncio
async def test_track_start_is_a_noop_without_a_tracked_entry():
    music = Music.__new__(Music)
    music._states = {7: GuildState()}  # now_playing is None
    event = SimpleNamespace(player=SimpleNamespace(guild=SimpleNamespace(id=7)), track=DummyTrack("X", "y"))

    await music.on_track_start(event)  # must not raise

    music._states = {}  # unknown guild entirely
    await music.on_track_start(event)


def test_track_key_prefers_stable_identifier_over_position_embedding_encoded_id():
    """Regression: Lavalink's encoded/id blob embeds the current playback
    position, so the SAME track's encoded string differs between
    track-start (position 0) and track-end (position ~= full duration).
    Comparing on encoded/id first made every http/fallback track's own
    real end-of-track event look "stale" and silently strand the queue.
    """
    music = Music.__new__(Music)
    start = SimpleNamespace(encoded="AAA...position0", id="AAA...position0", identifier="video-xyz")
    end = SimpleNamespace(encoded="AAA...positionFULL", id="AAA...positionFULL", identifier="video-xyz")

    assert music._track_key(start) == music._track_key(end)
    assert music._track_key(start) == "identifier:video-xyz"

    # Still falls back correctly when identifier truly isn't available.
    encoded_only = SimpleNamespace(encoded="blob-1", id=None, identifier=None)
    assert music._track_key(encoded_only) == "encoded:blob-1"
    assert music._track_key(None) is None


@pytest.mark.asyncio
async def test_autocomplete_never_offers_a_truncated_broken_uri():
    """Regression: a track's .uri can be a 1000+ char signed CDN stream
    link, not the clean webpage URL. Discord caps a choice's value at 100
    chars regardless, so blindly slicing it produced a corrupted,
    unresolvable string that silently played the wrong/metadata-less
    stream when selected. A too-long uri must fall back to the title.
    """
    long_uri = "https://rr11---sn-8xgp1vo-ab5d.googlevideo.com/videoplayback?" + "x" * 200
    short = SimpleNamespace(title="Se Me Nota (Agarrame)", duration=177_000, uri="https://youtu.be/abc123")
    long_ = SimpleNamespace(title="Chimbala x Omega - Se Me Nota", duration=178_000, uri=long_uri)

    music = Music.__new__(Music)
    music._autocomplete_cache = {}
    music._backend = SimpleNamespace(
        wait_ready=AsyncMock(),
        resolve_tracks=AsyncMock(return_value=[short, long_]),
    )

    choices = await music.play_autocomplete(SimpleNamespace(), "se me nota")

    values = list(choices.values())
    assert values[0] == "https://youtu.be/abc123"  # short uri: used as-is
    assert all(len(v) <= 100 for v in values)
    assert long_uri not in values  # never submit the corrupted, truncated URL
    assert values[1] == "Chimbala x Omega - Se Me Nota"  # falls back to the title
