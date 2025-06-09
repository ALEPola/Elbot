import asyncio

import nextcord
from nextcord.ext import commands

from cogs import music as music_cog


class DummyCtx:
    def __init__(self):
        self.guild = type("Guild", (), {"id": 123})()
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


def test_reorder_queue_updates(monkeypatch):
    monkeypatch.setattr(asyncio, "create_task", lambda *a, **k: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    q = asyncio.Queue()
    q.put_nowait({"title": "one"})
    q.put_nowait({"title": "two"})
    q.put_nowait({"title": "three"})
    cog.queue[123] = q

    ctx = DummyCtx()

    before = cog.queue[123]
    asyncio.run(music_cog.Music.reorder_queue.callback(cog, ctx, 1, 3))
    after = cog.queue[123]

    assert before is not after
    titles = [item["title"] for item in list(after._queue)]
    assert titles == ["two", "three", "one"]
    assert ctx.sent[-1] == "🔄 Moved song to position 3."
