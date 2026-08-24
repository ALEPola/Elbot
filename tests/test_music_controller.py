import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
