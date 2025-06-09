import asyncio
import importlib
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import aiohttp
import pytest

import nextcord
from nextcord.ext import commands, tasks

import elbot.config as config


def _setup_config(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    importlib.reload(config)
    # Inject attributes expected by the cog
    config.Config.BASE_DIR = config.BASE_DIR
    config.Config.ICS_URL = "http://example.com/f1.ics"
    config.Config.F1_CHANNEL_ID = None


def _create_bot(monkeypatch):
    monkeypatch.setattr(asyncio, "create_task", lambda *a, **k: None)
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *a, **k: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    monkeypatch.setattr(bot.loop, "create_task", lambda *a, **k: None)
    return bot, loop


def test_f1_commands_registered(monkeypatch):
    _setup_config(monkeypatch)
    bot, loop = _create_bot(monkeypatch)
    try:
        # Avoid network calls when the cog loads
        from cogs import F1 as f1
        monkeypatch.setattr(f1, "fetch_events", AsyncMock(return_value=[]))
        monkeypatch.setattr(f1.F1Cog, "get_schedule", lambda self: None)
        cog = f1.F1Cog(bot)
        bot.add_cog(cog)
        commands_list = bot.get_all_application_commands()
        names = {cmd.name for cmd in commands_list}
        assert "f1_schedule" in names
        assert "f1_countdown" in names
        assert "f1_results" in names
    finally:
        loop.close()


def test_reminder_triggers(monkeypatch):
    _setup_config(monkeypatch)
    bot, loop = _create_bot(monkeypatch)
    try:
        from cogs import F1 as f1

        dt = datetime.now(f1.LOCAL_TZ) + timedelta(minutes=30)
        monkeypatch.setattr(f1, "fetch_events", AsyncMock(return_value=[(dt, "Test GP")]))
        monkeypatch.setattr(f1.F1Cog, "get_schedule", lambda self: None)

        class DummyUser:
            def __init__(self):
                self.sent = []

            async def send(self, msg):
                self.sent.append(msg)

        dummy = DummyUser()
        monkeypatch.setattr(bot, "fetch_user", AsyncMock(return_value=dummy))

        cog = f1.F1Cog(bot)
        bot.add_cog(cog)
        cog.subscribers = {123}

        asyncio.run(cog.reminder_loop())

        assert dummy.sent
        assert "Test GP" in dummy.sent[0]
    finally:
        loop.close()


def test_fetch_events_client_error(monkeypatch):
    _setup_config(monkeypatch)
    from cogs import F1 as f1

    async def raise_error(*a, **k):
        raise aiohttp.ClientError("boom")

    monkeypatch.setattr(aiohttp.ClientSession, "get", raise_error)

    events = asyncio.run(f1.fetch_events(limit=1))
    assert events == []


@pytest.mark.asyncio
async def test_fetch_events_empty_url(monkeypatch):
    _setup_config(monkeypatch)
    config.Config.ICS_URL = ""
    from cogs import F1 as f1
    importlib.reload(f1)
    events = await f1.fetch_events()
    assert events == []


def test_fetch_race_results_client_error(monkeypatch):
    _setup_config(monkeypatch)
    from cogs import F1 as f1

    async def raise_error(*a, **k):
        raise aiohttp.ClientError("boom")

    monkeypatch.setattr(aiohttp.ClientSession, "get", raise_error)
    race, results = asyncio.run(f1.fetch_race_results())
    assert race is None and results == []
