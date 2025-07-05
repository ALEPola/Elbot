import asyncio
import nextcord
from nextcord.ext import commands

from cogs import music as music_cog

class DummyVoiceClient:
    def __init__(self):
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


def test_music_cog_unload_disconnects_voice(monkeypatch):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = music_cog.Music(bot)

    task = loop.create_task(asyncio.sleep(0))
    cog.timeout_tasks[123] = task

    vc = DummyVoiceClient()
    dummy_guild = type("Guild", (), {"voice_client": vc})()
    guilds = [dummy_guild]

    monkeypatch.setattr(commands.Bot, "guilds", property(lambda self: guilds))

    orig_create_task = bot.loop.create_task

    def fake_create_task(coro):
        task = orig_create_task(coro)
        loop.run_until_complete(task)
        return task

    monkeypatch.setattr(bot.loop, "create_task", fake_create_task)

    cog.cog_unload()
    loop.run_until_complete(asyncio.sleep(0))

    assert task.cancelled()
    assert vc.disconnected
    loop.close()
