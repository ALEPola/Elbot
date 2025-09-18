import asyncio
import importlib
import warnings
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import aiohttp
import pytest

import nextcord
from nextcord.ext import commands, tasks

import elbot.config as config


def _setup_config(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
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
        from elbot.cogs import F1 as f1
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
        from elbot.cogs import F1 as f1

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
    from elbot.cogs import F1 as f1

    def raise_error(self, url, *a, **k):
        class Resp:
            async def __aenter__(self_inner):
                raise aiohttp.ClientError("boom")

            async def __aexit__(self_inner, exc_type, exc, tb):
                pass

        return Resp()

    monkeypatch.setattr(aiohttp.ClientSession, "get", raise_error)

    events = asyncio.run(f1.fetch_events(limit=1))
    assert events == []


@pytest.mark.asyncio
async def test_fetch_events_empty_url(monkeypatch):
    _setup_config(monkeypatch)
    config.Config.ICS_URL = ""
    from elbot.cogs import F1 as f1
    importlib.reload(f1)
    events = await f1.fetch_events()
    assert events == []


@pytest.mark.asyncio
async def test_fetch_events_webcal(monkeypatch):
    _setup_config(monkeypatch)
    config.Config.ICS_URL = "webcal://example.com/f1.ics"
    from elbot.cogs import F1 as f1
    importlib.reload(f1)

    sample_ics = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Test GP\n"
        "DTSTART:29991231T000000Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR"
    )

    def dummy_get(self, url, *a, **k):
        class Resp:
            async def text(self):
                return sample_ics

            def raise_for_status(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        dummy_get.called_url = url
        return Resp()

    monkeypatch.setattr(aiohttp.ClientSession, "get", dummy_get)

    events = await f1.fetch_events(limit=1)
    assert dummy_get.called_url == "https://example.com/f1.ics"
    assert events and events[0][1] == "Test GP"


def test_fetch_race_results_client_error(monkeypatch):
    _setup_config(monkeypatch)
    from elbot.cogs import F1 as f1

    def raise_error(self, url, *a, **k):
        class Resp:
            async def __aenter__(self_inner):
                raise aiohttp.ClientError("boom")

            async def __aexit__(self_inner, exc_type, exc, tb):
                pass

        return Resp()

    monkeypatch.setattr(aiohttp.ClientSession, "get", raise_error)
    race, results = asyncio.run(f1.fetch_race_results())
    assert race is None and results == []


@pytest.mark.asyncio
async def test_fetch_events_no_unclosed_warning(monkeypatch):
    _setup_config(monkeypatch)
    from aiohttp import web

    sample_ics = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\n"
        "SUMMARY:Test GP\n"
        "DTSTART:29991231T000000Z\n"
        "END:VEVENT\n"
        "END:VCALENDAR"
    )

    async def handler(request):
        return web.Response(text=sample_ics)

    app = web.Application()
    app.router.add_get("/f1.ics", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    config.Config.ICS_URL = f"http://localhost:{port}/f1.ics"
    from elbot.cogs import F1 as f1
    importlib.reload(f1)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        events = await f1.fetch_events(limit=1)
        await f1.close_session()

    await runner.cleanup()

    assert events and events[0][1] == "Test GP"
    assert not any("Unclosed" in str(wr.message) for wr in w)


@pytest.mark.asyncio
async def test_fetch_race_results_no_unclosed_warning(monkeypatch):
    _setup_config(monkeypatch)
    from aiohttp import web

    data = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "raceName": "Test Race",
                        "Results": [
                            {
                                "position": "1",
                                "Driver": {"familyName": "Driver"},
                                "Constructor": {"name": "Team"},
                            }
                        ],
                    }
                ]
            }
        }
    }

    async def handler(request):
        return web.json_response(data)

    app = web.Application()
    app.router.add_get("/results.json", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    results_url = f"http://localhost:{port}/results.json"
    orig_get = aiohttp.ClientSession.get

    def local_get(self, url, *a, **k):
        if url == "https://ergast.com/api/f1/current/last/results.json":
            url = results_url
        return orig_get(self, url, *a, **k)

    monkeypatch.setattr(aiohttp.ClientSession, "get", local_get)
    from elbot.cogs import F1 as f1
    importlib.reload(f1)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        race_name, results = await f1.fetch_race_results()
        await f1.close_session()

    await runner.cleanup()

    assert race_name == "Test Race"
    assert results[0] == ("1", "Driver", "Team")
    assert not any("Unclosed" in str(wr.message) for wr in w)
