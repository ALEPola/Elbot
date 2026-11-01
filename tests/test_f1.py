import asyncio
import importlib
import json
import warnings
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import aiohttp
import nextcord
import pytest
from nextcord.ext import commands, tasks

import elbot.config as config
from elbot.f1 import data as f1data
from elbot.f1 import sessions as f1sessions
from elbot.f1 import store as f1store
from elbot.f1.sessions import PRACTICE, QUALIFYING, RACE, SPRINT, SPRINT_QUALIFYING, OTHER, Session

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def _setup_config(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    importlib.reload(config)
    # Inject attributes expected by the cog
    config.Config.BASE_DIR = config.BASE_DIR
    config.Config.ICS_URL = "http://example.com/f1.ics"
    config.Config.F1_CHANNEL_ID = None


@pytest.fixture
def f1(monkeypatch, tmp_path):
    _setup_config(monkeypatch)
    from elbot.cogs import F1 as module
    importlib.reload(module)
    monkeypatch.setattr(module, "SUBSCRIBERS_FILE", tmp_path / "subscribers.json")
    monkeypatch.setattr(module, "REMINDERS_FILE", tmp_path / "f1_reminders.json")
    return module


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *a, **k: None)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = commands.Bot(command_prefix="!", intents=nextcord.Intents.none(), loop=loop)
    monkeypatch.setattr(bot.loop, "create_task", lambda coro, *a, **k: coro.close())
    yield bot
    loop.close()


class DummyUser:
    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = fail_with

    async def send(self, msg):
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(msg)


def _make_cog(f1, bot, monkeypatch, sessions, user=None):
    cog = f1.F1Cog(bot)
    monkeypatch.setattr(cog.data, "schedule", AsyncMock(return_value=list(sessions)))
    user = user or DummyUser()
    monkeypatch.setattr(bot, "get_user", lambda uid: None)
    monkeypatch.setattr(bot, "fetch_user", AsyncMock(return_value=user))
    return cog, user


def _local_server(handlers):
    """Start an aiohttp app serving ``{path: handler}`` and return (runner, base_url)."""
    from aiohttp import web

    async def start():
        app = web.Application()
        for path, handler in handlers.items():
            app.router.add_get(path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return runner, f"http://localhost:{port}"

    return start()


# --- Commands -------------------------------------------------------------


def test_f1_commands_registered(f1, bot):
    bot.add_cog(f1.F1Cog(bot))
    names = {cmd.name for cmd in bot.get_all_application_commands()}
    assert {"f1_schedule", "f1_countdown", "f1_next", "f1_results", "f1_standings",
            "f1_subscribe", "f1_unsubscribe"} <= names


def _interaction(guild_id=None, user_id=1):
    return SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
        guild=SimpleNamespace(id=guild_id) if guild_id else None,
        user=SimpleNamespace(id=user_id),
    )


@pytest.mark.asyncio
async def test_results_command_reports_missing_results(f1, bot, monkeypatch):
    cog = f1.F1Cog(bot)
    monkeypatch.setattr(cog.data, "sprint", AsyncMock(return_value=None))
    interaction = _interaction()
    await cog.f1_results.callback(cog, interaction, session="sprint", round_=7)
    cog.data.sprint.assert_awaited_once_with(7)
    assert "No sprint results available for round 7" in interaction.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_results_command_reports_unavailable_api(f1, bot, monkeypatch):
    cog = f1.F1Cog(bot)
    monkeypatch.setattr(cog.data, "race_results", AsyncMock(side_effect=f1data.F1DataError("down")))
    interaction = _interaction()
    await cog.f1_results.callback(cog, interaction, session="race", round_=None)
    assert interaction.followup.send.call_args.args[0] == f1.UNAVAILABLE


@pytest.mark.asyncio
async def test_guild_restriction_applies_to_subscribe(f1, bot, monkeypatch):
    monkeypatch.setattr(f1, "GUILD_ID", 42)
    cog = f1.F1Cog(bot)
    interaction = _interaction(guild_id=7)
    await cog.f1_subscribe.callback(cog, interaction, sessions=None, lead=None)
    assert 1 not in cog.subscriptions
    assert interaction.followup.send.call_args.args[0] == "Not available in this server."


@pytest.mark.asyncio
async def test_subscribe_keeps_unchanged_preferences(f1, bot):
    cog = f1.F1Cog(bot)
    await cog.f1_subscribe.callback(cog, _interaction(), sessions="race", lead=None)
    await cog.f1_subscribe.callback(cog, _interaction(), sessions=None, lead="15m")
    assert cog.subscriptions.get(1) == f1store.Subscription("race", "15m")


# --- Session classification and schedule parsing --------------------------


@pytest.mark.parametrize("summary,kind", [
    ("Monaco Grand Prix - Practice 1", PRACTICE),
    ("F1: FP2 (Bahrain)", PRACTICE),
    ("Monaco Grand Prix - Qualifying", QUALIFYING),
    ("Chinese Grand Prix - Sprint Qualifying", SPRINT_QUALIFYING),
    ("Sprint Shootout", SPRINT_QUALIFYING),
    ("Chinese Grand Prix - Sprint", SPRINT),
    ("Sprint Race", SPRINT),
    ("Monaco Grand Prix - Race", RACE),
    ("FORMULA 1 GRAN PREMIO DE ESPAÑA 2026", RACE),
    ("Pre-season testing", OTHER),
])
def test_classify_session(summary, kind):
    assert f1sessions.classify_session(summary) == kind


def _ics(*events):
    body = "".join(f"BEGIN:VEVENT\r\n{e}END:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n"


def test_parse_ics_floating_all_day_and_malformed_events():
    text = _ics(
        "UID:a\r\nSUMMARY:Floating Race\r\nDTSTART:29991231T140000\r\n",
        "UID:b\r\nSUMMARY:Weekend\r\nDTSTART;VALUE=DATE:29991230\r\n",
        "UID:c\r\nSUMMARY:No start\r\n",
        "UID:d\r\nSUMMARY:Utc Qualifying\r\nDTSTART:29991230T120000Z\r\n",
    )
    sessions = f1sessions.parse_ics(text, NY)
    by_name = {s.name: s for s in sessions}
    assert set(by_name) == {"Floating Race", "Utc Qualifying"}
    # Floating times are read in the configured zone, not the host's.
    assert by_name["Floating Race"].start == datetime(2999, 12, 31, 14, tzinfo=NY)
    assert by_name["Utc Qualifying"].start == datetime(2999, 12, 30, 12, tzinfo=UTC)
    assert by_name["Utc Qualifying"].kind == QUALIFYING


def test_session_key_distinguishes_shared_start_and_ignores_name():
    start = datetime(2026, 5, 1, 12, tzinfo=UTC)
    assert Session(start, "A", PRACTICE).key != Session(start, "A", QUALIFYING).key
    assert Session(start, "ICS name", RACE).key == Session(start.astimezone(NY), "API name", RACE).key


RACE_WEEKEND = {
    "season": "2999", "round": "5", "raceName": "Test Grand Prix",
    "Circuit": {"circuitName": "Test Ring", "Location": {"locality": "Town", "country": "Land"}},
    "date": "2999-05-03", "time": "13:00:00Z",
    "FirstPractice": {"date": "2999-05-01", "time": "11:30:00Z"},
    "SprintQualifying": {"date": "2999-05-01", "time": "15:30:00Z"},
    "Sprint": {"date": "2999-05-02", "time": "10:00:00Z"},
    "Qualifying": {"date": "2999-05-02", "time": "14:00:00Z"},
}


def test_weekend_sessions_from_jolpica():
    sessions = f1sessions.weekend_sessions(RACE_WEEKEND, UTC)
    assert [s.kind for s in sessions] == [PRACTICE, SPRINT_QUALIFYING, SPRINT, QUALIFYING, RACE]
    assert sessions[-1].start == datetime(2999, 5, 3, 13, tzinfo=UTC)
    assert sessions[0].name == "Test Grand Prix - Practice 1"


def test_current_weekend_stays_on_race_until_it_finishes():
    past = dict(RACE_WEEKEND, round="4", date="2000-01-01")
    race_start = datetime(2999, 5, 3, 13, tzinfo=UTC)
    assert f1sessions.current_weekend([past, RACE_WEEKEND], race_start + timedelta(hours=1)) is RACE_WEEKEND
    assert f1sessions.current_weekend([past, RACE_WEEKEND], race_start + timedelta(hours=4)) is None


# --- Data client ----------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_keeps_last_good_data_when_refresh_fails(monkeypatch):
    client = f1data.F1Data("http://example.com/f1.ics", UTC, schedule_ttl=0)
    good = _ics("SUMMARY:Race\r\nDTSTART:29991231T000000Z\r\n")
    monkeypatch.setattr(client, "_get_text", AsyncMock(return_value=good))
    first = await client.schedule()
    assert len(first) == 1

    monkeypatch.setattr(client, "_get_text", AsyncMock(side_effect=aiohttp.ClientError("boom")))
    assert await client.schedule() == first
    # A broken feed body is also contained.
    monkeypatch.setattr(client, "_get_text", AsyncMock(return_value="not a calendar"))
    assert await client.schedule() == first


@pytest.mark.asyncio
async def test_schedule_failure_is_not_cached_as_empty(monkeypatch):
    client = f1data.F1Data("http://example.com/f1.ics", UTC)
    monkeypatch.setattr(client, "_get_text", AsyncMock(side_effect=asyncio.TimeoutError()))
    assert await client.schedule() == []
    monkeypatch.setattr(client, "_get_text", AsyncMock(return_value=_ics("SUMMARY:Race\r\nDTSTART:29991231T000000Z\r\n")))
    assert len(await client.schedule()) == 1


@pytest.mark.asyncio
async def test_schedule_uses_jolpica_without_ics(monkeypatch):
    client = f1data.F1Data("", UTC)
    payload = {"MRData": {"RaceTable": {"Races": [RACE_WEEKEND]}}}
    monkeypatch.setattr(client, "_get_json", AsyncMock(return_value=payload))
    sessions = await client.schedule()
    client._get_json.assert_awaited_once_with("current.json")
    assert len(sessions) == 5


def test_webcal_url_converted():
    assert f1data.F1Data("webcal://example.com/f1.ics", UTC).ics_url == "https://example.com/f1.ics"


RESULTS = {"MRData": {"RaceTable": {"Races": [{
    "season": "2999", "round": "5", "raceName": "Test Grand Prix",
    "Circuit": {"circuitName": "Test Ring", "Location": {"locality": "Town"}},
    "Results": [
        {"position": "1", "points": "25", "Driver": {"givenName": "Ann", "familyName": "One"},
         "Constructor": {"name": "Team A"}, "status": "Finished", "Time": {"time": "1:30:00.000"}},
        {"position": "2", "points": "18", "Driver": {"givenName": "Bo", "familyName": "Two"},
         "Constructor": {"name": "Team B"}, "status": "+1 Lap"},
    ],
}]}}}


def test_parse_race_results():
    table = f1data.parse_race_results(RESULTS)
    assert table.title == "2999 Test Grand Prix"
    assert table.subtitle == "Round 5 · Test Ring · Town"
    assert table.rows[0] == f1data.ResultRow("1", "Ann One", "Team A", "1:30:00.000", "25")
    assert table.rows[1].detail == "+1 Lap"


@pytest.mark.parametrize("payload", [
    {},
    {"MRData": {"RaceTable": {"Races": []}}},
    {"MRData": {"RaceTable": {"Races": [{"raceName": "Future GP", "Results": []}]}}},
])
def test_parse_race_results_empty(payload):
    assert f1data.parse_race_results(payload) is None


def test_parse_qualifying_uses_best_session_time():
    payload = {"MRData": {"RaceTable": {"Races": [{"raceName": "GP", "QualifyingResults": [
        {"position": "1", "Driver": {"familyName": "One"}, "Constructor": {"name": "A"},
         "Q1": "1:31.0", "Q2": "1:30.5", "Q3": "1:30.0"},
        {"position": "16", "Driver": {"familyName": "Out"}, "Constructor": {"name": "B"}, "Q1": "1:32.0"},
    ]}]}}}
    table = f1data.parse_qualifying(payload)
    assert [row.detail for row in table.rows] == ["1:30.0", "1:32.0"]


def test_parse_standings():
    drivers = {"MRData": {"StandingsTable": {"season": "2999", "StandingsLists": [{
        "season": "2999", "round": "12",
        "DriverStandings": [{"position": "1", "points": "300", "wins": "8",
                             "Driver": {"givenName": "Ann", "familyName": "One"},
                             "Constructors": [{"name": "Old"}, {"name": "Team A"}]}],
    }]}}}
    table = f1data.parse_standings(drivers)
    assert table.title == "2999 Drivers' Championship"
    assert table.subtitle == "After round 12"
    assert table.rows[0] == f1data.ResultRow("1", "Ann One", "Team A", "8 wins", "300")

    teams = {"MRData": {"StandingsTable": {"StandingsLists": [{
        "season": "2999", "round": "12",
        "ConstructorStandings": [{"position": "1", "points": "500", "wins": "10", "Constructor": {"name": "Team A"}}],
    }]}}}
    table = f1data.parse_standings(teams, constructors=True)
    assert table.title == "2999 Constructors' Championship"
    assert table.rows[0] == f1data.ResultRow("1", "", "Team A", "10 wins", "500")
    assert f1data.parse_standings({"MRData": {"StandingsTable": {"StandingsLists": []}}}) is None


@pytest.mark.asyncio
async def test_latest_sprint_round_skips_future_sprints(monkeypatch):
    client = f1data.F1Data("", UTC)
    races = [
        {"round": "2", "Sprint": {"date": "2000-03-14", "time": "03:00:00Z"}},
        {"round": "3"},
        {"round": "6", "Sprint": {"date": "2000-05-01", "time": "03:00:00Z"}},
        {"round": "9", "Sprint": {"date": "2999-07-01", "time": "03:00:00Z"}},
    ]
    monkeypatch.setattr(client, "season_races", AsyncMock(return_value=races))
    monkeypatch.setattr(client, "_get_json", AsyncMock(return_value={}))
    assert await client.sprint() is None
    client._get_json.assert_awaited_once_with("current/6/sprint.json")


@pytest.mark.asyncio
async def test_jolpica_requests_against_local_server():
    from aiohttp import web

    seen = []

    async def handler(request):
        seen.append((request.path, request.query.get("limit")))
        return web.json_response(RESULTS)

    runner, base = await _local_server({"/ergast/f1/current/last/results.json": handler,
                                        "/ergast/f1/current/3/results.json": handler})
    client = f1data.F1Data("", UTC, base_url=f"{base}/ergast/f1")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        latest = await client.race_results()
        await client.race_results()  # served from cache
        await client.race_results(3)
        await client.close()
    await runner.cleanup()

    assert latest.rows[0].driver == "Ann One"
    assert seen == [("/ergast/f1/current/last/results.json", "100"), ("/ergast/f1/current/3/results.json", "100")]
    assert not any("Unclosed" in str(wr.message) for wr in w)


@pytest.mark.asyncio
async def test_jolpica_errors_raise_data_error():
    from aiohttp import web

    async def broken(request):
        return web.Response(status=503)

    runner, base = await _local_server({"/current/driverStandings.json": broken})
    client = f1data.F1Data("", UTC, base_url=base)
    with pytest.raises(f1data.F1DataError):
        await client.driver_standings()
    await client.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_ics_fetch_against_local_server():
    from aiohttp import web

    async def handler(request):
        return web.Response(text=_ics("SUMMARY:Test GP\r\nDTSTART:29991231T000000Z\r\n"))

    runner, base = await _local_server({"/f1.ics": handler})
    client = f1data.F1Data(f"{base}/f1.ics", UTC)
    sessions = await client.schedule()
    await client.close()
    await runner.cleanup()
    assert sessions[0].name == "Test GP" and sessions[0].kind == RACE


# --- Upcoming sessions and announcements ----------------------------------


@pytest.mark.asyncio
async def test_cached_schedule_is_filtered_on_read(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    past = Session(now - timedelta(minutes=5), "Started", PRACTICE)
    future = Session(now + timedelta(hours=2), "Later", QUALIFYING)
    cog, _ = _make_cog(f1, bot, monkeypatch, [past, future])
    assert await cog.upcoming_sessions() == [future]


def test_format_countdown_never_negative(f1):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert f1.format_countdown(now - timedelta(minutes=3), now) == "0d 0h 0m"
    assert f1.format_countdown(now + timedelta(days=1, hours=2, minutes=5), now) == "1d 2h 5m"


def test_weekly_message_names_the_race_not_practice(f1):
    start = datetime(2999, 5, 1, 11, tzinfo=UTC)
    practice = Session(start, "GP - Practice 1", PRACTICE)
    race = Session(start + timedelta(days=2), "GP - Race", RACE)
    message = f1.F1Cog.weekly_message([practice, race])
    assert message.startswith("🏁 **Next F1 Race:** GP - Race")
    assert "First up:** GP - Practice 1" in message
    assert f1.F1Cog.weekly_message([Session(start, "Mystery", OTHER)]).startswith("🏎️ **Next F1 Session:** Mystery")
    assert f1.F1Cog.weekly_message([]) == "⚠️ No upcoming F1 sessions found."


# --- Reminders ------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminder_triggers(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    session = Session(now + timedelta(minutes=30), "Test GP", RACE)
    cog, user = _make_cog(f1, bot, monkeypatch, [session])
    cog.subscriptions.set(123, f1store.Subscription())

    await cog.check_reminders(now)

    assert len(user.sent) == 1 and "Test GP" in user.sent[0]


@pytest.mark.asyncio
async def test_reminder_not_resent_after_restart(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    session = Session(now + timedelta(minutes=30), "Test GP", RACE)
    cog, user = _make_cog(f1, bot, monkeypatch, [session])
    cog.subscriptions.set(123, f1store.Subscription())
    await cog.check_reminders(now)
    await cog.check_reminders(now + timedelta(minutes=1))

    restarted, user2 = _make_cog(f1, bot, monkeypatch, [session])
    await restarted.check_reminders(now + timedelta(minutes=2))

    assert len(user.sent) == 1
    assert user2.sent == []


@pytest.mark.asyncio
async def test_failed_reminder_is_retried_with_a_limit(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    session = Session(now + timedelta(minutes=50), "Test GP", RACE)
    flaky = DummyUser(fail_with=nextcord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom"))
    cog, _ = _make_cog(f1, bot, monkeypatch, [session], flaky)
    cog.subscriptions.set(123, f1store.Subscription())

    for minute in range(f1store.MAX_ATTEMPTS + 2):
        await cog.check_reminders(now + timedelta(minutes=minute))
    assert bot.fetch_user.await_count == f1store.MAX_ATTEMPTS

    # A retry that succeeds is delivered once.
    cog2, user = _make_cog(f1, bot, monkeypatch, [Session(session.start, "Quali", QUALIFYING)])
    cog2.subscriptions.set(123, f1store.Subscription())
    flaky.fail_with = None
    monkeypatch.setattr(bot, "fetch_user", AsyncMock(side_effect=[
        DummyUser(fail_with=nextcord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")), user, user]))
    for minute in range(3):
        await cog2.check_reminders(now + timedelta(minutes=minute))
    assert len(user.sent) == 1


@pytest.mark.asyncio
async def test_forbidden_dm_is_not_retried(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    session = Session(now + timedelta(minutes=30), "Test GP", RACE)
    closed = DummyUser(fail_with=nextcord.Forbidden(SimpleNamespace(status=403, reason="x"), "dms closed"))
    cog, _ = _make_cog(f1, bot, monkeypatch, [session], closed)
    cog.subscriptions.set(123, f1store.Subscription())
    await cog.check_reminders(now)
    await cog.check_reminders(now + timedelta(minutes=1))
    assert bot.fetch_user.await_count == 1


@pytest.mark.asyncio
async def test_sessions_sharing_a_start_time_both_remind(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    start = now + timedelta(minutes=30)
    cog, user = _make_cog(f1, bot, monkeypatch, [Session(start, "F1 Race", RACE), Session(start, "Other", OTHER)])
    cog.subscriptions.set(123, f1store.Subscription())
    await cog.check_reminders(now)
    assert len(user.sent) == 2


@pytest.mark.asyncio
async def test_reminders_respect_session_filter(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    start = now + timedelta(minutes=10)
    cog, user = _make_cog(f1, bot, monkeypatch, [Session(start, "FP1", PRACTICE), Session(start, "Quali", QUALIFYING)])
    cog.subscriptions.set(123, f1store.Subscription(sessions="competitive"))
    await cog.check_reminders(now)
    assert len(user.sent) == 1 and "Quali" in user.sent[0]


@pytest.mark.asyncio
async def test_day_and_hour_leads(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    start = now + timedelta(hours=30)
    cog, user = _make_cog(f1, bot, monkeypatch, [Session(start, "Race", RACE)])
    cog.subscriptions.set(123, f1store.Subscription(lead="1d+1h"))

    await cog.check_reminders(now)  # 30h out: nothing yet
    assert user.sent == []
    await cog.check_reminders(start - timedelta(hours=23))
    await cog.check_reminders(start - timedelta(hours=22))
    assert len(user.sent) == 1
    await cog.check_reminders(start - timedelta(minutes=59))
    assert len(user.sent) == 2


@pytest.mark.asyncio
async def test_late_start_sends_a_single_reminder(f1, bot, monkeypatch):
    now = datetime.now(f1.LOCAL_TZ)
    cog, user = _make_cog(f1, bot, monkeypatch, [Session(now + timedelta(minutes=20), "Race", RACE)])
    cog.subscriptions.set(123, f1store.Subscription(lead="1d+1h"))
    await cog.check_reminders(now)
    await cog.check_reminders(now + timedelta(minutes=1))
    assert len(user.sent) == 1


@pytest.mark.asyncio
async def test_reminder_loop_survives_errors(f1, bot, monkeypatch):
    cog = f1.F1Cog(bot)
    cog.subscriptions.set(123, f1store.Subscription())
    monkeypatch.setattr(cog.data, "schedule", AsyncMock(side_effect=RuntimeError("bad feed")))
    await cog.reminder_loop()  # must not raise


# --- Persistence ----------------------------------------------------------


def test_legacy_subscriber_list_migrates(tmp_path):
    path = tmp_path / "subscribers.json"
    path.write_text("[123, 456]")
    store = f1store.SubscriptionStore(path)
    assert store.get(123) == f1store.Subscription("all", "1h")
    store.set(789, f1store.Subscription("race", "15m"))
    saved = json.loads(path.read_text())
    assert saved["123"] == {"sessions": "all", "lead": "1h"}
    assert saved["789"] == {"sessions": "race", "lead": "15m"}
    assert f1store.SubscriptionStore(path).get(789).leads == (15,)


def test_corrupt_subscriber_file_is_kept_and_reported(tmp_path, caplog):
    path = tmp_path / "subscribers.json"
    path.write_text("[123,")
    store = f1store.SubscriptionStore(path)
    assert len(store) == 0
    assert "Could not read" in caplog.text
    asides = list(tmp_path.glob("subscribers.json.corrupt-*"))
    assert len(asides) == 1 and asides[0].read_text() == "[123,"


def test_reminder_log_prunes_old_sessions(tmp_path):
    log = f1store.ReminderLog(tmp_path / "f1_reminders.json")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    old = Session(now - timedelta(days=3), "Old", RACE)
    new = Session(now + timedelta(hours=1), "New", RACE)
    log.record([log.entry_key(old, 60, 1)], old, sent=True)
    log.record([log.entry_key(new, 60, 1)], new, sent=True)
    log.prune(now)
    log.save()
    reloaded = f1store.ReminderLog(tmp_path / "f1_reminders.json")
    assert reloaded.pending(log.entry_key(old, 60, 1))
    assert not reloaded.pending(log.entry_key(new, 60, 1))
