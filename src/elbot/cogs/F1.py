# cogs/f1.py

import logging
import os
from datetime import datetime, timedelta, time as dt_time, tzinfo
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import nextcord
from nextcord.ext import commands, tasks

from elbot.config import Config
from elbot.f1.data import F1Data, F1DataError, ResultTable
from elbot.f1.sessions import (
    KIND_LABELS,
    RACE,
    Session,
    current_weekend,
    discord_time,
    upcoming,
    weekend_sessions,
)
from elbot.f1.store import (
    LEAD_LABELS,
    SESSION_FILTER_LABELS,
    ReminderLog,
    Subscription,
    SubscriptionStore,
)
from elbot.utils import safe_reply

logger = logging.getLogger("elbot.f1")

# Configuration loaded from environment via Config.
# With ``ICS_URL`` empty, the schedule comes from the Jolpica API instead.
ICS_URL = Config.ICS_URL  # URL to the ICS calendar feed (webcal:// is accepted)
# Convert "0" to None when F1_CHANNEL_ID isn't configured
CHANNEL_ID = Config.F1_CHANNEL_ID or None  # Channel ID for weekly updates
GUILD_ID = Config.GUILD_ID  # Optional guild restriction
# Use UTC if LOCAL_TIMEZONE is unset or blank
# Set LOCAL_TIMEZONE to an IANA zone like "America/New_York".
# It sets when the weekly post goes out and how zone-less feed times are read;
# users see session times in their own zone through Discord timestamps.
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TIMEZONE") or "UTC")

# Persistence files (in project root, gitignored)
SUBSCRIBERS_FILE = Path(Config.BASE_DIR) / "subscribers.json"
REMINDERS_FILE = Path(Config.BASE_DIR) / "f1_reminders.json"

F1_RED = 0xE10600
MAX_SCHEDULE = 15
UNAVAILABLE = "⚠️ Couldn't reach the F1 data service. Try again later."

SESSION_CHOICES = {label.capitalize(): key for key, label in SESSION_FILTER_LABELS.items()}
LEAD_CHOICES = {label.capitalize(): key for key, label in LEAD_LABELS.items()}
RESULT_CHOICES = {"Race": "race", "Qualifying": "qualifying", "Sprint": "sprint"}
STANDINGS_CHOICES = {"Drivers": "drivers", "Constructors": "constructors"}


class AwareZone(tzinfo):
    """tzinfo adapter to allow zoneinfo zones with nextcord tasks."""

    def __init__(self, zone: ZoneInfo):
        self.zone = zone

    def utcoffset(self, dt=None):
        if dt is None:
            dt = datetime.now(self.zone)
        return self.zone.utcoffset(dt)

    def dst(self, dt=None):
        if dt is None:
            dt = datetime.now(self.zone)
        return self.zone.dst(dt)

    def tzname(self, dt=None):
        if dt is None:
            dt = datetime.now(self.zone)
        return self.zone.tzname(dt)


def format_countdown(dt, now=None):
    """Return a countdown string 'Xd Xh Xm' until datetime `dt`."""
    delta = max(dt - (now or datetime.now(LOCAL_TZ)), timedelta(0))
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h {minutes}m"


def format_when(session: Session) -> str:
    return f"{discord_time(session.start)} ({discord_time(session.start, 'R')})"


def results_embed(table: ResultTable, heading: str) -> nextcord.Embed:
    lines = []
    for row in table.rows:
        who = row.driver or row.team
        if row.driver and row.team:
            who = f"{row.driver} — {row.team}"
        extras = [part for part in (row.detail, f"{row.points} pts" if row.points else "") if part]
        lines.append(f"**{row.position}.** {who}" + (f" · {' · '.join(extras)}" if extras else ""))
    embed = nextcord.Embed(title=f"{table.title} {heading}".strip(), description="\n".join(lines), color=F1_RED)
    if table.subtitle:
        embed.set_footer(text=table.subtitle)
    return embed


class F1Cog(commands.Cog):
    """Cog for Formula 1 schedule, results, standings, and reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = F1Data(ICS_URL, LOCAL_TZ)
        self.subscriptions = SubscriptionStore(SUBSCRIBERS_FILE)
        self.reminders = ReminderLog(REMINDERS_FILE)
        self.reminders.prune(datetime.now(LOCAL_TZ))
        self.weekly_update.start()
        self.reminder_loop.start()

    def cog_unload(self):
        self.weekly_update.cancel()
        self.reminder_loop.cancel()
        self.bot.loop.create_task(self.data.close())

    async def upcoming_sessions(self, limit: Optional[int] = None) -> list[Session]:
        # Filter on every read: the cached schedule is fetched once an hour
        # and would otherwise keep sessions that have already started.
        return upcoming(await self.data.schedule(), datetime.now(LOCAL_TZ), limit)

    async def _blocked(self, interaction: nextcord.Interaction) -> bool:
        if GUILD_ID and interaction.guild and interaction.guild.id != GUILD_ID:
            await safe_reply(interaction, "Not available in this server.", ephemeral=True)
            return True
        return False

    @tasks.loop(time=dt_time(hour=12, tzinfo=AwareZone(LOCAL_TZ)))
    async def weekly_update(self):
        """Every Sunday at 12:00, post the next Grand Prix to the configured channel."""
        now = datetime.now(LOCAL_TZ)
        if now.weekday() != 6 or CHANNEL_ID is None:
            return
        channel = self.bot.get_channel(CHANNEL_ID)
        if channel is None:
            logger.error("F1Cog: CHANNEL_ID not found.")
            return
        await channel.send(self.weekly_message(await self.upcoming_sessions()))

    @staticmethod
    def weekly_message(sessions: list[Session]) -> str:
        if not sessions:
            return "⚠️ No upcoming F1 sessions found."
        race = next((s for s in sessions if s.kind == RACE), None)
        if race is None:
            # The feed's names could not be classified; don't call a practice a race.
            return f"🏎️ **Next F1 Session:** {sessions[0].name}\n📅 **When:** {format_when(sessions[0])}"
        message = f"🏁 **Next F1 Race:** {race.name}\n📅 **When:** {format_when(race)}"
        if sessions[0] is not race:
            message += f"\n🟢 **First up:** {sessions[0].name} — {format_when(sessions[0])}"
        return message

    @weekly_update.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        """Check every minute and DM subscribers whose reminder window has opened."""
        try:
            await self.check_reminders()
        except Exception:
            # Keep the loop alive; the next tick retries.
            logger.exception("F1 reminder check failed")

    async def check_reminders(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(LOCAL_TZ)
        subs = self.subscriptions.items()
        if not subs:
            return
        horizon = now + timedelta(minutes=max(max(sub.leads) for _, sub in subs))
        changed = False
        for session in upcoming(await self.data.schedule(), now):
            if session.start > horizon:
                break
            until = session.start - now
            for user_id, sub in subs:
                if session.kind not in sub.kinds:
                    continue
                # Every lead time whose window has opened is covered by one
                # message, so a late start doesn't send a burst of reminders.
                keys = [
                    key
                    for lead in sub.leads
                    if until <= timedelta(minutes=lead)
                    for key in [self.reminders.entry_key(session, lead, user_id)]
                    if self.reminders.pending(key)
                ]
                if not keys:
                    continue
                sent, permanent = await self._send_reminder(user_id, session, now)
                self.reminders.record(keys, session, sent=sent, permanent=permanent)
                changed = True
        if changed:
            self.reminders.prune(now)
            self.reminders.save()

    async def _send_reminder(self, user_id: int, session: Session, now: datetime) -> tuple[bool, bool]:
        """Return (sent, permanent_failure)."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(
                f"⏰ Reminder: **{session.name}** starts in {format_countdown(session.start, now)} "
                f"({discord_time(session.start, 't')})"
            )
            return True, False
        except (nextcord.Forbidden, nextcord.NotFound) as e:
            logger.warning("F1Cog reminder to %s cannot be delivered: %s", user_id, e)
            return False, True
        except Exception as e:
            logger.error("F1Cog reminder failed for %s: %s", user_id, e)
            return False, False

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    @nextcord.slash_command(name="f1_schedule", description="Show upcoming F1 sessions")
    async def f1_schedule(
        self,
        interaction: nextcord.Interaction,
        count: int = nextcord.SlashOption(default=5, min_value=1, max_value=MAX_SCHEDULE),
    ):
        await interaction.response.defer(with_message=True)
        if await self._blocked(interaction):
            return
        sessions = await self.upcoming_sessions(count)
        if not sessions:
            await safe_reply(interaction, "⚠️ No upcoming F1 sessions found.")
            return
        embed = nextcord.Embed(title=f"Next {len(sessions)} F1 Sessions", color=F1_RED)
        for session in sessions:
            embed.add_field(name=session.name, value=format_when(session), inline=False)
        await safe_reply(interaction, embed=embed)

    @nextcord.slash_command(
        name="f1_countdown", description="Countdown to next F1 session"
    )
    async def f1_countdown(self, interaction: nextcord.Interaction):
        await interaction.response.defer(with_message=True)
        if await self._blocked(interaction):
            return
        sessions = await self.upcoming_sessions()
        if not sessions:
            await safe_reply(interaction, "⚠️ No upcoming F1 sessions found.")
            return
        lines = [f"⏱ **{sessions[0].name}** starts in {format_countdown(sessions[0].start)} "
                 f"({discord_time(sessions[0].start)})"]
        race = next((s for s in sessions if s.kind == RACE), None)
        if race is not None and race is not sessions[0]:
            lines.append(f"🏁 **{race.name}** starts in {format_countdown(race.start)} ({discord_time(race.start)})")
        await safe_reply(interaction, "\n".join(lines))

    @nextcord.slash_command(name="f1_next", description="Full timetable for the current or next race weekend")
    async def f1_next(self, interaction: nextcord.Interaction):
        await interaction.response.defer(with_message=True)
        if await self._blocked(interaction):
            return
        try:
            races = await self.data.season_races()
        except F1DataError:
            await safe_reply(interaction, UNAVAILABLE)
            return
        now = datetime.now(LOCAL_TZ)
        race = current_weekend(races, now)
        if race is None:
            await safe_reply(interaction, "🏁 The season is over — no more race weekends this year.")
            return
        circuit = race.get("Circuit", {})
        where = circuit.get("Location", {})
        place = ", ".join(p for p in (where.get("locality"), where.get("country")) if p)
        embed = nextcord.Embed(
            title=f"Round {race.get('round', '?')} · {race.get('raceName', 'Grand Prix')}",
            description=" — ".join(p for p in (circuit.get("circuitName"), place) if p),
            url=race.get("url") or None,
            color=F1_RED,
        )
        for session in weekend_sessions(race, LOCAL_TZ):
            label = session.name.rsplit(" - ", 1)[-1] or KIND_LABELS[session.kind]
            if session.start <= now:
                value = f"✅ {discord_time(session.start)}"
            else:
                value = format_when(session)
            embed.add_field(name=label, value=value, inline=False)
        await safe_reply(interaction, embed=embed)

    @nextcord.slash_command(name="f1_results", description="Race, qualifying, or sprint results")
    async def f1_results(
        self,
        interaction: nextcord.Interaction,
        session: str = nextcord.SlashOption(
            description="Which session (default: race)", choices=RESULT_CHOICES, default="race", required=False
        ),
        round_: Optional[int] = nextcord.SlashOption(
            name="round", description="Round number this season (default: latest)",
            min_value=1, max_value=30, default=None, required=False,
        ),
    ):
        await interaction.response.defer(with_message=True)
        if await self._blocked(interaction):
            return
        fetch = {"race": self.data.race_results, "qualifying": self.data.qualifying, "sprint": self.data.sprint}
        try:
            table = await fetch.get(session, self.data.race_results)(round_)
        except F1DataError:
            await safe_reply(interaction, UNAVAILABLE)
            return
        if table is None:
            which = f"round {round_}" if round_ else "the latest round"
            await safe_reply(interaction, f"⚠️ No {session} results available for {which} yet.")
            return
        heading = {"race": "Results", "qualifying": "Qualifying", "sprint": "Sprint Results"}.get(session, "Results")
        await safe_reply(interaction, embed=results_embed(table, heading))

    @nextcord.slash_command(name="f1_standings", description="Drivers' or constructors' championship standings")
    async def f1_standings(
        self,
        interaction: nextcord.Interaction,
        championship: str = nextcord.SlashOption(choices=STANDINGS_CHOICES, default="drivers", required=False),
    ):
        await interaction.response.defer(with_message=True)
        if await self._blocked(interaction):
            return
        try:
            if championship == "constructors":
                table = await self.data.constructor_standings()
            else:
                table = await self.data.driver_standings()
        except F1DataError:
            await safe_reply(interaction, UNAVAILABLE)
            return
        if table is None:
            await safe_reply(interaction, "⚠️ No standings available yet this season.")
            return
        await safe_reply(interaction, embed=results_embed(table, ""))

    @nextcord.slash_command(
        name="f1_subscribe", description="DM reminders before F1 sessions"
    )
    async def f1_subscribe(
        self,
        interaction: nextcord.Interaction,
        sessions: Optional[str] = nextcord.SlashOption(
            description="Which sessions to be reminded about", choices=SESSION_CHOICES, default=None, required=False
        ),
        lead: Optional[str] = nextcord.SlashOption(
            description="How long before each session", choices=LEAD_CHOICES, default=None, required=False
        ),
    ):
        await interaction.response.defer(with_message=True, ephemeral=True)
        if await self._blocked(interaction):
            return
        current = self.subscriptions.get(interaction.user.id) or Subscription()
        sub = Subscription(sessions or current.sessions, lead or current.lead)
        self.subscriptions.set(interaction.user.id, sub)
        await safe_reply(
            interaction,
            f"✅ You will receive DM reminders for {sub.describe()}.",
            ephemeral=True,
        )

    @nextcord.slash_command(name="f1_unsubscribe", description="Stop session reminders")
    async def f1_unsubscribe(self, interaction: nextcord.Interaction):
        await interaction.response.defer(with_message=True, ephemeral=True)
        if await self._blocked(interaction):
            return
        self.subscriptions.remove(interaction.user.id)
        await safe_reply(
            interaction,
            "🛑 You have been unsubscribed.",
            ephemeral=True,
        )


def setup(bot: commands.Bot):
    """Add the F1 cog and preload the schedule."""
    cog = F1Cog(bot)
    bot.add_cog(cog)
    logger.info("✅ Loaded F1Cog")
    bot.loop.create_task(cog.data.schedule())  # Preload schedule on startup
    logger.info("F1 schedule preloaded.")
