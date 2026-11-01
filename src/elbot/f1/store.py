"""Persistent F1 subscriptions and reminder delivery state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from elbot.file_io import atomic_write_text

from .sessions import PRACTICE, QUALIFYING, RACE, SPRINT, SPRINT_QUALIFYING, OTHER, Session

logger = logging.getLogger("elbot.f1")

SESSION_FILTERS = {
    "all": frozenset({PRACTICE, SPRINT_QUALIFYING, SPRINT, QUALIFYING, RACE, OTHER}),
    "competitive": frozenset({SPRINT_QUALIFYING, SPRINT, QUALIFYING, RACE}),
    "race": frozenset({RACE}),
}
SESSION_FILTER_LABELS = {
    "all": "all sessions",
    "competitive": "qualifying, sprints and races",
    "race": "races only",
}
# Minutes before the session start.
LEAD_OPTIONS = {
    "15m": (15,),
    "1h": (60,),
    "1d": (1440,),
    "1d+1h": (1440, 60),
}
LEAD_LABELS = {
    "15m": "15 minutes before",
    "1h": "1 hour before",
    "1d": "1 day before",
    "1d+1h": "1 day and 1 hour before",
}
DEFAULT_SESSIONS = "all"
DEFAULT_LEAD = "1h"

MAX_ATTEMPTS = 3
KEEP_HISTORY = timedelta(days=2)


def _load_json(path: Path) -> Any:
    """Read a JSON file, or ``None`` when it does not exist.

    A file that cannot be parsed is moved aside and reported instead of being
    silently replaced on the next save.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        aside = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d%H%M%S}")
        logger.error("Could not read %s (%s); moved it to %s", path, exc, aside.name)
        try:
            path.replace(aside)
        except OSError as move_exc:
            logger.error("Could not move %s aside: %s", path, move_exc)
        return None


def _save_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


@dataclass(frozen=True)
class Subscription:
    sessions: str = DEFAULT_SESSIONS
    lead: str = DEFAULT_LEAD

    @property
    def kinds(self) -> frozenset[str]:
        return SESSION_FILTERS.get(self.sessions, SESSION_FILTERS[DEFAULT_SESSIONS])

    @property
    def leads(self) -> tuple[int, ...]:
        return LEAD_OPTIONS.get(self.lead, LEAD_OPTIONS[DEFAULT_LEAD])

    def describe(self) -> str:
        return f"{SESSION_FILTER_LABELS.get(self.sessions, self.sessions)}, {LEAD_LABELS.get(self.lead, self.lead)}"


class SubscriptionStore:
    """User ID to reminder preferences.

    Older files hold a bare list of user IDs; those users keep the original
    behaviour (all sessions, one hour before).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._subs: dict[int, Subscription] = {}
        raw = _load_json(self.path)
        if isinstance(raw, list):
            self._subs = {int(uid): Subscription() for uid in raw}
        elif isinstance(raw, dict):
            for uid, prefs in raw.items():
                prefs = prefs if isinstance(prefs, dict) else {}
                self._subs[int(uid)] = Subscription(prefs.get("sessions", DEFAULT_SESSIONS),
                                                    prefs.get("lead", DEFAULT_LEAD))
        elif raw is not None:
            logger.error("Ignoring unexpected subscriber data in %s", self.path)

    def __contains__(self, user_id: int) -> bool:
        return user_id in self._subs

    def __len__(self) -> int:
        return len(self._subs)

    def items(self) -> list[tuple[int, Subscription]]:
        return list(self._subs.items())

    def get(self, user_id: int) -> Subscription | None:
        return self._subs.get(user_id)

    def set(self, user_id: int, sub: Subscription) -> None:
        self._subs[user_id] = sub
        self.save()

    def remove(self, user_id: int) -> bool:
        removed = self._subs.pop(user_id, None) is not None
        if removed:
            self.save()
        return removed

    def save(self) -> None:
        _save_json(self.path, {str(uid): {"sessions": s.sessions, "lead": s.lead} for uid, s in self._subs.items()})


class ReminderLog:
    """Which reminders have been delivered, so restarts never resend them.

    Each entry is one (session, lead time, user). Failed deliveries are
    retried on later checks up to ``MAX_ATTEMPTS`` unless the failure is
    permanent (for example, the user does not accept DMs).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        raw = _load_json(self.path)
        self._entries: dict[str, dict[str, Any]] = raw if isinstance(raw, dict) else {}

    @staticmethod
    def entry_key(session: Session, lead: int, user_id: int) -> str:
        return f"{session.key}#{lead}#{user_id}"

    def pending(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return True
        return entry.get("status") == "retry" and entry.get("attempts", 0) < MAX_ATTEMPTS

    def record(self, keys: Iterable[str], session: Session, *, sent: bool, permanent: bool = False) -> None:
        for key in keys:
            attempts = self._entries.get(key, {}).get("attempts", 0) + 1
            if sent:
                status = "sent"
            elif permanent or attempts >= MAX_ATTEMPTS:
                status = "failed"
            else:
                status = "retry"
            self._entries[key] = {"start": session.start.isoformat(), "status": status, "attempts": attempts}

    def prune(self, now: datetime) -> None:
        cutoff = now - KEEP_HISTORY
        stale = []
        for key, entry in self._entries.items():
            try:
                if datetime.fromisoformat(entry["start"]) < cutoff:
                    stale.append(key)
            except (KeyError, TypeError, ValueError):
                stale.append(key)
        for key in stale:
            del self._entries[key]

    def save(self) -> None:
        _save_json(self.path, self._entries)
