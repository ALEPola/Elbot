import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


def test_connect_nodes_uses_env(monkeypatch):
    recorded = {}

    async def fake_create_node(*, bot, host, port, password, **_):
        recorded['host'] = host
        recorded['port'] = port
        recorded['password'] = password
        return object()

    monkeypatch.setattr(music_cog.wavelink.NodePool, 'create_node', fake_create_node)
    monkeypatch.setenv('LAVALINK_HOST', 'example.com')
    monkeypatch.setenv('LAVALINK_PORT', '9999')
    monkeypatch.setenv('LAVALINK_PASSWORD', 'secret')

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix='!', intents=intents, loop=loop)

    cog = music_cog.Music(bot)
    loop.run_until_complete(cog.connect_task)
    assert recorded['host'] == 'example.com'
    assert recorded['port'] == 9999
    assert recorded['password'] == 'secret'
    loop.close()


def test_ensure_voice_calls_player_connect(monkeypatch):
    recorded = {}

    async def fake_player_connect(self, *, guild_id, channel, **_):
        recorded['guild_id'] = guild_id
        recorded['channel'] = channel

    monkeypatch.setattr(music_cog.wavelink.Player, 'connect', fake_player_connect)

    class DummyNode:
        def __init__(self):
            self._players = {}
            self._inactive_channel_tokens = None
            self._inactive_player_timeout = None
            self.client = bot

    monkeypatch.setattr(music_cog.wavelink.NodePool, 'get_node', lambda *a, **k: DummyNode())

    async def fake_create_node(*a, **k):
        return DummyNode()

    monkeypatch.setattr(music_cog.wavelink.NodePool, 'create_node', fake_create_node)

    async def fake_wait_until_ready(self):
        return None

    monkeypatch.setattr(commands.Bot, 'wait_until_ready', fake_wait_until_ready)

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
