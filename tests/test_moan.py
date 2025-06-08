import asyncio
import nextcord
from nextcord.ext import commands

from elbot.utils import load_all_cogs


def test_moan_command_registered(monkeypatch):
    monkeypatch.setattr(asyncio, "create_task", lambda *args, **kwargs: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    load_all_cogs(bot, cogs_dir="cogs")
    commands_list = bot.get_all_application_commands()
    assert any(cmd.name == "moan" for cmd in commands_list)
