import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from elbot.cogs.music import GuildState, Music
from elbot.live.memory import UserMemory
from elbot.live.tools import build_tools
from elbot.live.schema import validate_arguments
from elbot.music.actions import Actor, ActionDenied, current_actor, fingerprint
from elbot.music.core import MusicQueue


def track(uid=1, key="one"):
    return SimpleNamespace(id=key, requested_by=uid, requester_display=f"user{uid}",
                           handle=SimpleNamespace(title=key, author="artist", duration=120000))


@pytest.fixture
def setup():
    channel = SimpleNamespace(id=20, voice_states={1: object(), 2: object(), 3: object(), 99: object()})
    members = {uid: SimpleNamespace(id=uid, display_name=f"user{uid}", bot=uid == 99, roles=[],
               guild_permissions=SimpleNamespace(administrator=False, manage_guild=False)) for uid in channel.voice_states}
    guild = SimpleNamespace(id=10, fetch_member=AsyncMock(side_effect=lambda uid: members[uid]))
    state = GuildState(player=SimpleNamespace(channel=channel, pause=AsyncMock(), resume=AsyncMock(),
                       set_volume=AsyncMock(), seek=AsyncMock()), owner_id=1, last_channel_id=40)
    music = Music.__new__(Music)
    music._states = {10: state}
    music._backend = SimpleNamespace()
    music.fallback = SimpleNamespace(build_queue_entry=AsyncMock(return_value=track(2, "new")))
    music._ensure_playing = AsyncMock()
    music._refresh_now_playing = AsyncMock()
    music._skip_current = AsyncMock(return_value=(True, "Skipped the current track."))
    music._stop = AsyncMock()
    music._disconnect = AsyncMock()
    return music, guild, state, members


def actor(uid=1, valid=lambda: True):
    return Actor(uid, 10, 20, valid)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["skip", "clear", "stop", "disconnect", "shuffle", "loop", "seek", "autoplay", "fair"])
async def test_listener_cannot_use_privileged_actions(setup, action):
    music, guild, state, _ = setup
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(2), action, confirmed=fingerprint(state))
    music._skip_current.assert_not_awaited()
    music._stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_dj_role_and_owner_permissions_are_checked_from_current_member(setup, monkeypatch):
    music, guild, state, members = setup
    monkeypatch.setenv("ELBOT_DJ_ROLE_IDS", "456")
    members[2].roles = [SimpleNamespace(id=456)]
    assert (await music.actions.execute(guild, actor(2), "skip"))["skipped"]
    members[2].roles = []
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(2), "skip")
    assert (await music.actions.execute(guild, actor(1), "skip"))["skipped"]
    guild.fetch_member.assert_awaited()


@pytest.mark.asyncio
async def test_absent_listener_stale_context_and_wrong_channel_fail_closed(setup):
    music, guild, state, _ = setup
    state.player.channel.voice_states.pop(2)
    for who in (actor(2), actor(1, lambda: False), Actor(1, 10, 21), Actor(99, 10, 20), None):
        with pytest.raises(ActionDenied):
            await music.actions.execute(guild, who, "pause")
    state.player.pause.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_rechecks_departure_role_and_scope_before_enqueue(setup, monkeypatch):
    music, guild, state, members = setup
    monkeypatch.setenv("ELBOT_DJ_ROLE_IDS", "456")
    members[2].roles = [SimpleNamespace(id=456)]

    async def resolve(*args, **kwargs):
        members[2].roles = []
        return track(2)

    music.fallback.build_queue_entry.side_effect = resolve
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(2), "play", {"query": "song", "play_next": True})
    assert len(state.queue) == 0
    async def leave(*args, **kwargs):
        state.player.channel.voice_states.pop(2)
        return track(2)
    music.fallback.build_queue_entry.side_effect = leave
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(2), "play", {"query": "song"})
    assert len(state.queue) == 0


@pytest.mark.asyncio
async def test_request_identity_is_fixed_even_if_controller_speaker_changes(setup):
    music, guild, state, _ = setup
    controller = SimpleNamespace(active_speaker=3, active_name="someone else")
    tools = build_tools(music, guild, controller)
    token = current_actor.set(actor(2))
    try:
        result = await tools["play_track"]({"query": "new song", "play_next": None})
    finally:
        current_actor.reset(token)
    assert result == {"queued": True, "title": "new", "author": "artist", "queue_position": 1}
    assert state.queue.peek().requested_by == 2
    assert state.queue.peek().requester_display == "user2"


@pytest.mark.asyncio
async def test_no_bot_identity_fallback(setup):
    music, guild, _, _ = setup
    result = await build_tools(music, guild)["play_track"]({"query": "song"})
    assert "authenticated" in result["error"]
    music.fallback.build_queue_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_and_pending_limits_shared_across_calls(setup, monkeypatch):
    music, guild, state, _ = setup
    monkeypatch.setenv("ELBOT_REQUESTS_PER_MINUTE", "2")
    for i in range(2):
        music.fallback.build_queue_entry.return_value = track(2, str(i))
        await music.actions.execute(guild, actor(2), "play", {"query": "song"})
        state.queue.clear()
    with pytest.raises(ActionDenied, match="limit"):
        await music.actions.execute(guild, actor(2), "play", {"query": "song"})
    state.request_times.clear()
    monkeypatch.setenv("ELBOT_MAX_QUEUED_PER_USER", "1")
    state.queue.add(track(2))
    with pytest.raises(ActionDenied, match="maximum"):
        await music.actions.execute(guild, actor(2), "play", {"query": "song"})


@pytest.mark.asyncio
async def test_votes_are_unique_human_only_and_reset_on_track_change(setup):
    music, guild, state, _ = setup
    state.now_playing = track(1)
    assert not (await music.actions.execute(guild, actor(2), "vote"))["skipped"]
    assert not (await music.actions.execute(guild, actor(2), "vote"))["skipped"]
    assert (await music.actions.execute(guild, actor(3), "vote"))["skipped"]
    music._skip_current.assert_awaited_once()
    state.now_playing = track(1, "other")
    assert not (await music.actions.execute(guild, actor(3), "vote"))["skipped"]
    assert state.skip_votes == {3}


@pytest.mark.asyncio
async def test_remove_own_only_and_limited_volume(setup):
    music, guild, state, _ = setup
    state.queue.add(track(1, "a"))
    state.queue.add(track(2, "b"))
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(2), "remove", {"start": 1, "end": 2})
    assert (await music.actions.execute(guild, actor(2), "remove", {"start": 2, "end": 2}))["removed"] == 1
    assert (await music.actions.execute(guild, actor(2), "volume", {"level": 80}))["volume"] == 80
    for level in (10, 110, "80", True, 201):
        with pytest.raises(ActionDenied):
            await music.actions.execute(guild, actor(2), "volume", {"level": level})
    await music.actions.execute(guild, actor(1), "volume", {"level": 150})
    assert state.volume == 150


@pytest.mark.asyncio
async def test_pause_resume_and_failed_volume_preserve_state(setup):
    music, guild, state, _ = setup
    assert (await music.actions.execute(guild, actor(2), "pause"))["paused"]
    assert not (await music.actions.execute(guild, actor(2), "resume"))["paused"]
    state.player.set_volume.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        await music.actions.execute(guild, actor(1), "volume", {"level": 50})
    assert state.volume == 100


@pytest.mark.asyncio
async def test_confirmation_rejects_changed_queue_and_expired_actor(setup):
    music, guild, state, _ = setup
    state.queue.add(track(2))
    expected = fingerprint(state)
    state.queue.add(track(3, "another"))
    with pytest.raises(ActionDenied, match="changed"):
        await music.actions.execute(guild, actor(), "clear", confirmed=expected)
    with pytest.raises(ActionDenied):
        await music.actions.execute(guild, actor(), "clear")
    assert len(state.queue) == 2
    result = await music.actions.execute(guild, actor(), "clear", confirmed=fingerprint(state))
    assert result["cleared"] == 2


def interaction(guild, uid):
    return SimpleNamespace(guild=guild, user=SimpleNamespace(id=uid),
                           response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
                           edit_original_message=AsyncMock())


@pytest.mark.asyncio
async def test_button_is_requester_bound_single_use_and_rechecks_roles(setup):
    music, guild, state, members = setup
    state.queue.add(track(2))
    send = AsyncMock()
    await music.actions.confirm(guild, actor(), "clear", send)
    view = music.actions.pending[(10, 1)]
    await view.approve.callback(interaction(guild, 2))
    assert len(state.queue) == 1
    await asyncio.gather(view.approve.callback(interaction(guild, 1)), view.approve.callback(interaction(guild, 1)))
    assert len(state.queue) == 0
    assert view.used
    state.queue.add(track(2))
    await music.actions.confirm(guild, actor(), "clear", send)
    view = music.actions.pending[(10, 1)]
    state.owner_id = 3
    await view.approve.callback(interaction(guild, 1))
    assert len(state.queue) == 1


@pytest.mark.asyncio
async def test_expired_cancelled_and_replaced_confirmation_do_not_execute(setup):
    music, guild, state, _ = setup
    state.queue.add(track(2))
    for mode in ("expired", "cancelled", "replaced"):
        await music.actions.confirm(guild, actor(), "clear", AsyncMock())
        view = music.actions.pending[(10, 1)]
        if mode == "expired":
            view.expires = 0
        elif mode == "cancelled":
            await view.cancel.callback(interaction(guild, 1))
        else:
            await music.actions.confirm(guild, actor(), "clear", AsyncMock())
        await view.approve.callback(interaction(guild, 1))
        assert len(state.queue) == 1
    for view in music.actions.pending.values():
        view.stop()


def test_fair_rotation_matches_visible_order_and_preserves_priority_recovery():
    q = MusicQueue(fair=True)
    for user, key in [(1,"a1"), (1,"a2"), (1,"a3"), (2,"b1"), (2,"b2"), (3,"c1")]:
        q.add(track(user, key))
    assert [t.id for t in q.snapshot()] == ["a1", "b1", "c1", "a2", "b2", "a3"]
    assert q.pop_next().id == "a1"
    q.add(track(1, "a4"))
    assert q.peek().id == "b1"
    q.add_next(track(1, "retry"))
    assert q.pop_next().id == "retry"
    assert q.pop_next().id == "b1"
    q.set_fair(False)
    q.add(track(4, "d1"))
    assert q.snapshot()[-1].id == "d1"


@pytest.mark.parametrize("name,args", [
    ("play_track", {"query": "song"}), ("play_track", {"query": "song", "play_next": "false"}),
    ("skip_track", {"user_id": 1}), ("skip_track", []), ("set_volume", {"level": True}),
    ("set_volume", {"level": 201}), ("remember_about_user", {"note": "x" * 201}),
])
def test_schema_rejects_forged_extra_missing_and_wrong_typed_arguments(name, args):
    with pytest.raises(ValueError):
        validate_arguments(name, args)


@pytest.mark.asyncio
async def test_memory_uses_authenticated_requester_not_mutable_active_speaker(setup):
    music, guild, _, _ = setup
    memory = UserMemory(None)
    tools = build_tools(music, guild, SimpleNamespace(active_speaker=3), memory)
    token = current_actor.set(actor(2))
    try:
        assert (await tools["remember_about_user"]({"note": "likes reggaeton"}))["remembered"]
        assert (await tools["recall_about_user"]({}))["notes"] == ["likes reggaeton"]
    finally:
        current_actor.reset(token)
    assert memory.recall(3) == []
    assert "error" in await tools["remember_about_user"]({"note": "anything"})

@pytest.mark.asyncio
async def test_local_listener_cannot_bypass_live_stop_permissions():
    from elbot.cogs.listen import Listen
    live = SimpleNamespace(controllers={10: object()})
    cog = Listen(SimpleNamespace(get_cog=lambda name: live))
    interaction = SimpleNamespace(guild=SimpleNamespace(id=10),
                                 response=SimpleNamespace(send_message=AsyncMock()))
    await Listen.stop.callback(cog, interaction)
    assert '/live stop' in interaction.response.send_message.call_args.args[0]
