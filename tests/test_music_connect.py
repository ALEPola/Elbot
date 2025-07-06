import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


def test_connect_nodes_uses_env(monkeypatch):
    recorded = {}

    async def fake_connect(*, client, nodes):
        node = list(nodes)[0]
        recorded['uri'] = node.uri
        recorded['password'] = node.password

    monkeypatch.setattr(music_cog.wavelink.Pool, 'connect', fake_connect)
    monkeypatch.setenv('LAVALINK_HOST', 'example.com')
    monkeypatch.setenv('LAVALINK_PORT', '9999')
    monkeypatch.setenv('LAVALINK_PASSWORD', 'secret')

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix='!', intents=intents, loop=loop)

    cog = music_cog.Music(bot)
    loop.run_until_complete(cog.connect_task)
    assert recorded['uri'] == 'http://example.com:9999'
    assert recorded['password'] == 'secret'
    loop.close()
