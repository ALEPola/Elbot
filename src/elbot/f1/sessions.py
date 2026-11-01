"""F1 session model, classification, and schedule parsing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Optional

from icalendar import Calendar

logger = logging.getLogger("elbot.f1")

PRACTICE = "practice"
SPRINT_QUALIFYING = "sprint_qualifying"
SPRINT = "sprint"
QUALIFYING = "qualifying"
RACE = "race"
OTHER = "other"

KIND_LABELS = {
    PRACTICE: "Practice",
    SPRINT_QUALIFYING: "Sprint Qualifying",
    SPRINT: "Sprint",
    QUALIFYING: "Qualifying",
    RACE: "Race",
    OTHER: "Session",
}

# Checked in order: "Sprint Qualifying" must win over "Sprint" and
# "Qualifying", and "Grand Prix - Practice 1" must not count as a race.
_KIND_PATTERNS = (
    (SPRINT_QUALIFYING, re.compile(r"sprint\s*(qualifying|quali|shootout)|\bsq\b")),
    (SPRINT, re.compile(r"sprint")),
    (QUALIFYING, re.compile(r"qualifying|\bquali\b")),
    (PRACTICE, re.compile(r"practice|\bfp\s?[1-3]\b")),
    (RACE, re.compile(r"\brace\b|grand prix|gran premio|\bgp\b")),
)

# Jolpica schedule keys for the sessions before the race, with display names.
_JOLPICA_SESSIONS = (
    ("FirstPractice", PRACTICE, "Practice 1"),
    ("SecondPractice", PRACTICE, "Practice 2"),
    ("ThirdPractice", PRACTICE, "Practice 3"),
    ("SprintQualifying", SPRINT_QUALIFYING, "Sprint Qualifying"),
    ("SprintShootout", SPRINT_QUALIFYING, "Sprint Shootout"),
    ("Sprint", SPRINT, "Sprint"),
    ("Qualifying", QUALIFYING, "Qualifying"),
)

# How long a race weekend counts as "current" after the race starts.
RACE_DURATION = timedelta(hours=3)


def classify_session(summary: str) -> str:
    """Return the session kind for a calendar summary."""
    text = summary.lower()
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return OTHER


@dataclass(frozen=True)
class Session:
    start: datetime
    name: str
    kind: str

    @property
    def key(self) -> str:
        """Stable identity used for reminder delivery state.

        Start time plus kind distinguishes sessions that share a start time
        and stays the same whichever feed supplied the session.
        """
        stamp = self.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")
        if self.kind == OTHER:
            return f"{stamp}|{self.kind}|{self.name}"
        return f"{stamp}|{self.kind}"


def upcoming(sessions: Iterable[Session], now: datetime, limit: Optional[int] = None) -> list[Session]:
    """Return sessions starting after ``now`` in start order."""
    future = sorted((s for s in sessions if s.start > now), key=lambda s: s.start)
    return future if limit is None else future[:limit]


def parse_ics(text: str, tz: tzinfo) -> list[Session]:
    """Parse timed events from an ICS feed.

    Floating (zone-less) times are read in ``tz``. All-day events carry no
    start time, so they are skipped rather than treated as midnight sessions.
    A malformed event is logged and skipped without affecting the others.
    """
    cal = Calendar.from_ical(text)
    sessions = []
    for comp in cal.walk("VEVENT"):
        try:
            start_prop = comp.get("DTSTART")
            if start_prop is None:
                raise ValueError("missing DTSTART")
            start = start_prop.dt
            if not isinstance(start, datetime):
                if isinstance(start, date):
                    continue
                raise ValueError(f"unsupported DTSTART {start!r}")
            if start.tzinfo is None:
                start = start.replace(tzinfo=tz)
            summary = str(comp.get("SUMMARY", "")).strip()
            sessions.append(Session(start.astimezone(tz), summary, classify_session(summary)))
        except Exception as exc:
            logger.warning("Skipping malformed F1 calendar event %r: %s", comp.get("UID"), exc)
    return sessions


def _jolpica_start(entry: dict[str, Any], tz: tzinfo) -> Optional[datetime]:
    day = entry.get("date")
    if not day:
        return None
    clock = (entry.get("time") or "00:00:00Z").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(f"{day}T{clock}").astimezone(tz)
    except ValueError:
        logger.warning("Skipping F1 session with bad date %r %r", day, entry.get("time"))
        return None


def weekend_sessions(race: dict[str, Any], tz: tzinfo) -> list[Session]:
    """Return every session of a Jolpica race weekend in start order."""
    race_name = race.get("raceName", "Grand Prix")
    sessions = []
    for key, kind, label in _JOLPICA_SESSIONS:
        if key in race:
            start = _jolpica_start(race[key], tz)
            if start:
                sessions.append(Session(start, f"{race_name} - {label}", kind))
    start = _jolpica_start(race, tz)
    if start:
        sessions.append(Session(start, f"{race_name} - Race", RACE))
    return sorted(sessions, key=lambda s: s.start)


def schedule_from_races(races: Iterable[dict[str, Any]], tz: tzinfo) -> list[Session]:
    sessions = []
    for race in races:
        sessions.extend(weekend_sessions(race, tz))
    return sorted(sessions, key=lambda s: s.start)


def current_weekend(races: Iterable[dict[str, Any]], now: datetime) -> Optional[dict[str, Any]]:
    """Return the race weekend in progress, or the next one."""
    for race in races:
        start = _jolpica_start(race, now.tzinfo or timezone.utc)
        if start and start + RACE_DURATION > now:
            return race
    return None


def discord_time(dt: datetime, style: str = "F") -> str:
    """Discord timestamp markup, rendered in each viewer's own time zone."""
    return f"<t:{int(dt.timestamp())}:{style}>"
