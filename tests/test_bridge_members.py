"""BridgePlayer must find channel occupants without the privileged members intent."""

from types import SimpleNamespace

import pytest

from elbot.live.bridge_player import BridgePlayer


class FakeGuild:
    def __init__(self, cached, fetchable):
        self.id = 1
        self._cached = cached
        self._fetchable = fetchable
        self.fetch_calls = []

    def get_member(self, uid):
        return self._cached.get(uid)

    async def fetch_member(self, uid):
        self.fetch_calls.append(uid)
        try:
            return self._fetchable[uid]
        except KeyError:
            raise LookupError(uid)


def member(uid, bot=False):
    return SimpleNamespace(id=uid, bot=bot, display_name=f"user{uid}")


def player(guild, voice_ids, bot_id=99):
    p = BridgePlayer.__new__(BridgePlayer)
    p.guild = guild
    p.client = SimpleNamespace(user=SimpleNamespace(id=bot_id))
    p.channel = SimpleNamespace(id=2, voice_states={uid: object() for uid in voice_ids})
    p._member_cache = {}
    p._fetching = set()
    p._voice_ids = set()
    return p


@pytest.mark.asyncio
async def test_uncached_occupants_are_fetched_once_and_bots_excluded():
    guild = FakeGuild(cached={99: member(99, bot=True)}, fetchable={10: member(10), 20: member(20, bot=True)})
    p = player(guild, voice_ids={99, 10, 20, 30})

    assert p._human_members() == set()
    await p._refresh_members()
    assert p._human_members() == {10}
    assert sorted(guild.fetch_calls) == [10, 20, 30]

    await p._refresh_members()
    assert sorted(guild.fetch_calls) == [10, 20, 30, 30]  # only the unresolved id is retried
    assert p._member(10).display_name == "user10"


@pytest.mark.asyncio
async def test_cache_drops_members_who_left_and_prefers_guild_cache():
    guild = FakeGuild(cached={}, fetchable={10: member(10)})
    p = player(guild, voice_ids={10})
    await p._refresh_members()
    assert 10 in p._member_cache

    p.channel.voice_states = {}
    await p._refresh_members()
    assert p._member_cache == {}
    assert p._human_members() == set()

    guild._cached[10] = member(10)
    p.channel.voice_states = {10: object()}
    assert p._member(10) is guild._cached[10]
