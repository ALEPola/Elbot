import asyncio

from cogs import music as music_cog


class FakeNode:
    def __init__(self, *, uri, password):
        self.uri = uri
        self.password = password


def test_connect_nodes_uses_env(monkeypatch):
    recorded = {}

    async def fake_connect(*, client, nodes):
        node = list(nodes)[0]
        recorded['uri'] = node.uri
        recorded['password'] = node.password

    monkeypatch.setattr(music_cog.Config, 'LAVALINK_HOST', 'example.com')
    monkeypatch.setattr(music_cog.Config, 'LAVALINK_PORT', 9999)
    monkeypatch.setattr(music_cog.Config, 'LAVALINK_PASSWORD', 'secret')

    monkeypatch.setattr(music_cog.wavelink.Pool, 'connect', fake_connect)
    monkeypatch.setattr(music_cog.wavelink, 'Node', FakeNode)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    class DummyBot:
        def __init__(self, loop):
            self.loop = loop

        async def wait_until_ready(self):
            return

    bot = DummyBot(loop)

    cog = music_cog.Music(bot)
    loop.run_until_complete(cog.connect_task)
    assert recorded['uri'] == 'http://example.com:9999'
    assert recorded['password'] == 'secret'
    loop.close()


def test_connect_nodes_logs_failure(monkeypatch, caplog):
    async def fake_connect(**kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(music_cog.Config, 'LAVALINK_HOST', 'bad')
    monkeypatch.setattr(music_cog.Config, 'LAVALINK_PORT', 1)
    monkeypatch.setattr(music_cog.Config, 'LAVALINK_PASSWORD', 'pw')

    monkeypatch.setattr(music_cog.wavelink.Pool, 'connect', fake_connect)
    monkeypatch.setattr(music_cog.wavelink, 'Node', FakeNode)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    class DummyBot:
        def __init__(self, loop):
            self.loop = loop

        async def wait_until_ready(self):
            return

    bot = DummyBot(loop)

    with caplog.at_level('WARNING'):
        cog = music_cog.Music(bot)
        loop.run_until_complete(cog.connect_task)

    assert 'Unable to connect to Lavalink' in caplog.text
    loop.close()
