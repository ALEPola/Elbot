"""F1 data sources: the configured ICS feed and the Jolpica (Ergast successor) API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any, Optional

import aiohttp
from cachetools import TTLCache

from .sessions import Session, parse_ics, schedule_from_races

logger = logging.getLogger("elbot.f1")

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
FETCH_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, ValueError)


class F1DataError(Exception):
    """A source could not be fetched or parsed."""


@dataclass
class ResultRow:
    position: str
    driver: str
    team: str
    detail: str = ""
    points: str = ""


@dataclass
class ResultTable:
    title: str
    rows: list[ResultRow] = field(default_factory=list)
    subtitle: str = ""


def _driver_name(driver: dict[str, Any]) -> str:
    return " ".join(p for p in (driver.get("givenName"), driver.get("familyName")) if p) or "Unknown"


def _race_title(race: dict[str, Any]) -> str:
    return f"{race.get('season', '')} {race.get('raceName', 'Grand Prix')}".strip()


def _race_subtitle(race: dict[str, Any]) -> str:
    circuit = race.get("Circuit", {})
    where = circuit.get("Location", {})
    parts = [f"Round {race['round']}" if race.get("round") else "", circuit.get("circuitName", ""),
             where.get("locality", "")]
    return " · ".join(p for p in parts if p)


def _single_race(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    races = data.get("MRData", {}).get("RaceTable", {}).get("Races") or []
    return races[0] if races else None


def parse_race_results(data: dict[str, Any], key: str = "Results", limit: int = 10) -> Optional[ResultTable]:
    """Parse a Jolpica race or sprint results payload, or ``None`` when empty."""
    race = _single_race(data)
    if not race or not race.get(key):
        return None
    rows = []
    for entry in race[key][:limit]:
        detail = entry.get("Time", {}).get("time") or entry.get("status", "")
        rows.append(ResultRow(
            entry.get("position", "?"),
            _driver_name(entry.get("Driver", {})),
            entry.get("Constructor", {}).get("name", ""),
            detail,
            entry.get("points", ""),
        ))
    return ResultTable(_race_title(race), rows, _race_subtitle(race))


def parse_qualifying(data: dict[str, Any], limit: int = 10) -> Optional[ResultTable]:
    race = _single_race(data)
    if not race or not race.get("QualifyingResults"):
        return None
    rows = []
    for entry in race["QualifyingResults"][:limit]:
        best = entry.get("Q3") or entry.get("Q2") or entry.get("Q1") or ""
        rows.append(ResultRow(
            entry.get("position", "?"),
            _driver_name(entry.get("Driver", {})),
            entry.get("Constructor", {}).get("name", ""),
            best,
        ))
    return ResultTable(_race_title(race), rows, _race_subtitle(race))


def parse_standings(data: dict[str, Any], constructors: bool = False) -> Optional[ResultTable]:
    table = data.get("MRData", {}).get("StandingsTable", {})
    lists = table.get("StandingsLists") or []
    if not lists:
        return None
    standings = lists[0]
    rows = []
    if constructors:
        for entry in standings.get("ConstructorStandings", []):
            rows.append(ResultRow(entry.get("position", "-"), "", entry.get("Constructor", {}).get("name", ""),
                                  f"{entry.get('wins', '0')} wins", entry.get("points", "0")))
    else:
        for entry in standings.get("DriverStandings", []):
            teams = entry.get("Constructors") or [{}]
            rows.append(ResultRow(entry.get("position", "-"), _driver_name(entry.get("Driver", {})),
                                  teams[-1].get("name", ""), f"{entry.get('wins', '0')} wins",
                                  entry.get("points", "0")))
    if not rows:
        return None
    kind = "Constructors'" if constructors else "Drivers'"
    season = standings.get("season", table.get("season", ""))
    return ResultTable(f"{season} {kind} Championship".strip(), rows, f"After round {standings.get('round', '?')}")


class F1Data:
    """Fetches and caches F1 data.

    The session schedule comes from ``ics_url`` when configured, otherwise
    from Jolpica. The last good schedule is kept when a refresh fails, so a
    transient outage does not empty the schedule or stop reminders.
    """

    def __init__(self, ics_url: str, tz: tzinfo, *, base_url: str = JOLPICA_BASE,
                 schedule_ttl: int = 3600, results_ttl: int = 600):
        if ics_url.startswith("webcal://"):
            ics_url = "https://" + ics_url[len("webcal://"):]
        self.ics_url = ics_url
        self.tz = tz
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._schedule: TTLCache = TTLCache(maxsize=1, ttl=schedule_ttl)
        self._last_schedule: list[Session] = []
        self._responses: TTLCache = TTLCache(maxsize=64, ttl=results_ttl)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_text(self, url: str) -> str:
        session = await self._http()
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()

    async def _get_json(self, path: str) -> dict[str, Any]:
        if path in self._responses:
            return self._responses[path]
        url = f"{self.base_url}/{path}"
        try:
            session = await self._http()
            async with session.get(url, params={"limit": "100"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except FETCH_ERRORS as exc:
            logger.error("F1 API request %s failed: %s", path, exc)
            raise F1DataError(str(exc)) from exc
        if not isinstance(data, dict):
            raise F1DataError(f"unexpected response for {path}")
        self._responses[path] = data
        return data

    async def season_races(self) -> list[dict[str, Any]]:
        data = await self._get_json("current.json")
        return data.get("MRData", {}).get("RaceTable", {}).get("Races") or []

    async def _fetch_schedule(self) -> list[Session]:
        if self.ics_url:
            try:
                return parse_ics(await self._get_text(self.ics_url), self.tz)
            except Exception as exc:
                logger.error("Failed to fetch F1 calendar feed: %s", exc)
                raise F1DataError(str(exc)) from exc
        return schedule_from_races(await self.season_races(), self.tz)

    async def schedule(self) -> list[Session]:
        """Return all known sessions; callers filter out the past with ``upcoming``."""
        if "schedule" in self._schedule:
            return self._schedule["schedule"]
        try:
            sessions = await self._fetch_schedule()
        except F1DataError:
            return self._last_schedule
        if not sessions and self._last_schedule:
            logger.warning("F1 schedule refresh returned no sessions; keeping the previous schedule")
            return self._last_schedule
        self._schedule["schedule"] = self._last_schedule = sessions
        return sessions

    async def race_results(self, round_: Optional[int] = None) -> Optional[ResultTable]:
        data = await self._get_json(f"current/{round_ or 'last'}/results.json")
        return parse_race_results(data)

    async def qualifying(self, round_: Optional[int] = None) -> Optional[ResultTable]:
        data = await self._get_json(f"current/{round_ or 'last'}/qualifying.json")
        return parse_qualifying(data)

    async def sprint(self, round_: Optional[int] = None) -> Optional[ResultTable]:
        if round_ is None:
            round_ = await self._latest_sprint_round()
            if round_ is None:
                return None
        data = await self._get_json(f"current/{round_}/sprint.json")
        return parse_race_results(data, key="SprintResults")

    async def _latest_sprint_round(self) -> Optional[int]:
        # Jolpica pages by result row, so the season-wide sprint list is cut
        # off; find the latest sprint weekend in the schedule instead.
        now = datetime.now(self.tz)
        latest = None
        for race in await self.season_races():
            sprint = race.get("Sprint")
            if not sprint or not race.get("round"):
                continue
            day = sprint.get("date", "")
            clock = (sprint.get("time") or "00:00:00Z").replace("Z", "+00:00")
            try:
                start = datetime.fromisoformat(f"{day}T{clock}")
            except ValueError:
                continue
            if start <= now:
                latest = int(race["round"])
        return latest

    async def driver_standings(self) -> Optional[ResultTable]:
        return parse_standings(await self._get_json("current/driverStandings.json"))

    async def constructor_standings(self) -> Optional[ResultTable]:
        return parse_standings(await self._get_json("current/constructorStandings.json"), constructors=True)
