import asyncio
import nextcord
from nextcord.ext import commands

from elbot.utils import load_all_cogs


def test_load_all_cogs(monkeypatch):
    # Avoid requiring a running event loop for create_task
    monkeypatch.setattr(asyncio, "create_task", lambda *args, **kwargs: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    load_all_cogs(bot, cogs_dir="cogs")
    expected = {"ChatCog", "ImageCog", "UtilityCog", "Music"}
    assert expected.issubset(set(bot.cogs.keys()))
