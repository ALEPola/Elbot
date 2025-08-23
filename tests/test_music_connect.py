import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


def test_connect_nodes_uses_env(monkeypatch):
    recorded = {}

    async def fake_connect(*, nodes, client=None, cache_capacity=None):
        node = nodes[0]
        recorded['uri'] = node.uri
        recorded['password'] = node.password
        recorded['identifier'] = node.identifier
        node.status = music_cog.wavelink.NodeStatus.CONNECTED
        return {node.identifier: node}

    monkeypatch.setattr(music_cog.wavelink.Pool, 'connect', fake_connect)
    monkeypatch.setenv('LAVALINK_HOST', 'example.com')
    monkeypatch.setenv('LAVALINK_PORT', '9999')
    monkeypatch.setenv('LAVALINK_PASSWORD', 'secret')

    async def fake_wait_until_ready(self):
        return None

    monkeypatch.setattr(commands.Bot, 'wait_until_ready', fake_wait_until_ready)
    monkeypatch.setattr(music_cog.wavelink.Pool, 'is_connected', lambda: False, raising=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix='!', intents=intents, loop=loop)

    cog = music_cog.Music(bot)
    loop.run_until_complete(cog.connect_task)
    assert recorded['uri'] == 'http://example.com:9999'
    assert recorded['password'] == 'secret'
    assert recorded['identifier'] == 'MAIN'
    loop.close()


def test_ensure_voice_calls_player_connect(monkeypatch):
    recorded = {}

    class DummyPlayer:
        def __init__(self, client, channel):
            self.client = client
            self.channel = channel

        async def connect(self, *, guild_id, channel, **_):
            recorded['guild_id'] = guild_id
            recorded['channel'] = channel

    monkeypatch.setattr(music_cog.wavelink, 'Player', DummyPlayer)

    class DummyNode:
        status = music_cog.wavelink.NodeStatus.CONNECTED

        def __init__(self):
            self.client = bot

    monkeypatch.setattr(music_cog.wavelink.Pool, 'get_node', lambda *a, **k: DummyNode())

    async def fake_connect(*, nodes, client=None, cache_capacity=None):
        return {"MAIN": DummyNode()}

    monkeypatch.setattr(music_cog.wavelink.Pool, 'connect', fake_connect)

    async def fake_wait_until_ready(self):
        return None

    monkeypatch.setattr(commands.Bot, 'wait_until_ready', fake_wait_until_ready)
    monkeypatch.setattr(music_cog.wavelink.Pool, 'is_connected', lambda: False, raising=False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix='!', intents=intents, loop=loop)
    cog = music_cog.Music(bot)
    loop.run_until_complete(cog.connect_task)

    guild = type('Guild', (), {'id': 123, 'voice_client': None})()

    class DummyChannel:
        def __init__(self, guild):
            self.guild = guild

        async def connect(self, *, cls):
            player = cls(bot, self)
            await player.connect(guild_id=self.guild.id, channel=self)
            return player

    channel = DummyChannel(guild)
    user = type('User', (), {'voice': type('VS', (), {'channel': channel})()})()
    interaction = type('Interaction', (), {
        'user': user,
        'guild': guild,
        'response': type('Resp', (), {'send_message': lambda *a, **k: None})()
    })()

    loop.run_until_complete(cog.ensure_voice(interaction))

    assert recorded['guild_id'] == guild.id
    assert recorded['channel'] is channel
    loop.close()
