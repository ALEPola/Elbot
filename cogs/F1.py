# cogs/f1.py

import os
import json
import logging
import aiohttp
from icalendar import Calendar
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from cachetools import TTLCache

from elbot.config import Config

logger = logging.getLogger("elbot.f1")

# Configuration loaded from environment via Config
# If ``ICS_URL`` is empty, event fetching will be skipped.
ICS_URL = Config.ICS_URL  # URL to the ICS calendar feed
# Convert "0" to None when F1_CHANNEL_ID isn't configured
CHANNEL_ID = Config.F1_CHANNEL_ID or None  # Channel ID for weekly updates
GUILD_ID = Config.GUILD_ID  # Optional guild restriction
LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TIMEZONE", "UTC"))

# Path to subscriber persistence file (in project root)
SUBSCRIBERS_FILE = os.path.join(Config.BASE_DIR, "subscribers.json")


def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_subscribers(subscribers):
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subscribers), f)


async def fetch_events(limit=10):
    """Fetch and parse the next up to `limit` F1 sessions from the ICS feed.

    Returns an empty list if ``ICS_URL`` is not configured.
    """
    if not ICS_URL:
        logger.warning("ICS_URL not configured; skipping event fetch")
        return []
    try:
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(ICS_URL)
            resp.raise_for_status()
            cal = Calendar.from_ical(await resp.text())
    except aiohttp.ClientError as e:
        logger.error("Failed to fetch F1 schedule: %s", e)
        return []

    now = datetime.now(LOCAL_TZ)
    events = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        summary = str(comp.get("SUMMARY", ""))
        dt = comp.get("DTSTART").dt
        if not isinstance(dt, datetime):
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
        if dt > now:
            events.append((dt, summary))

    events.sort(key=lambda x: x[0])
    return events[:limit]


async def fetch_race_results():
    """Fetch the latest race results from the Ergast API."""
    url = "https://ergast.com/api/f1/current/last/results.json"
    try:
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            resp.raise_for_status()
            data = await resp.json()
    except aiohttp.ClientError as e:
        logger.error("Failed to fetch F1 results: %s", e)
        return None, []

    race = data.get("MRData", {}).get("RaceTable", {}).get("Races", [{}])[0]
    race_name = race.get("raceName", "")
    results = []
    for entry in race.get("Results", [])[:10]:
        pos = entry.get("position")
        driver = entry.get("Driver", {}).get("familyName")
        team = entry.get("Constructor", {}).get("name")
        results.append((pos, driver, team))
    return race_name, results


def format_countdown(dt):
    """Return a countdown string 'Xd Xh Xm' until datetime `dt`."""
    delta = dt - datetime.now(LOCAL_TZ)
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h {minutes}m"


def format_event_details(events):
    """Return a list of (name, human-readable datetime) tuples."""
    return [(name, dt.strftime("%A, %b %d %I:%M %p %Z")) for dt, name in events]


class F1Cog(commands.Cog):
    """Cog for Formula 1 schedule, countdown, and reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.subscribers = load_subscribers()
        self.schedule_cache = TTLCache(maxsize=1, ttl=3600)
        self.sent_reminders = set()
        if ICS_URL:
            self.weekly_update.start()
            self.reminder_loop.start()
        else:
            logger.warning("ICS_URL not configured; F1 reminders disabled")

    def cog_unload(self):
        self.weekly_update.cancel()
        self.reminder_loop.cancel()

    async def get_schedule(self):
        if "schedule" not in self.schedule_cache:
            self.schedule_cache["schedule"] = await fetch_events(limit=10)
        return self.schedule_cache["schedule"]

    @tasks.loop(time=dt_time(hour=12, tzinfo=LOCAL_TZ))
    async def weekly_update(self):
        """Every Sunday at 12:00, post the next Grand Prix to the configured channel."""
        now = datetime.now(LOCAL_TZ)
        if now.weekday() != 6 or CHANNEL_ID is None:
            return
        channel = self.bot.get_channel(CHANNEL_ID)
        if channel is None:
            logger.error("F1Cog: CHANNEL_ID not found.")
            return

        events = await fetch_events(limit=1)
        if not events:
            await channel.send("⚠️ No upcoming Grand Prix found.")
            return

        dt, name = events[0]
        await channel.send(
            f"🏁 **Next F1 Race:** {name}\n"
            f"📅 **When:** {dt.strftime('%A, %b %d %I:%M %p %Z')}"
        )

    @weekly_update.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        """Check every minute and DM subscribers if a session starts within 1 hour."""
        now = datetime.now(LOCAL_TZ)
        for dt, name in await fetch_events(limit=5):
            delta = dt - now
            if timedelta(0) < delta <= timedelta(hours=1):
                if dt not in self.sent_reminders:
                    for user_id in list(self.subscribers):
                        try:
                            user = await self.bot.fetch_user(user_id)
                            await user.send(
                                f"⏰ Reminder: **{name}** starts in {format_countdown(dt)}"
                            )
                        except Exception as e:
                            logger.error(f"F1Cog reminder failed for {user_id}: {e}")
                    self.sent_reminders.add(dt)
            elif delta <= timedelta(0) and dt in self.sent_reminders:
                self.sent_reminders.discard(dt)

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Remove subscriber DMs when bot leaves a guild."""
        # no-op: subscribers are by user DM, not guild

    @nextcord.slash_command(name="f1_schedule", description="Show upcoming F1 sessions")
    async def f1_schedule(self, interaction: nextcord.Interaction, count: int = 5):
        if GUILD_ID and interaction.guild and interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "Not available in this server.", ephemeral=True
            )
        await interaction.response.defer()
        events = await fetch_events(limit=count)
        embed = nextcord.Embed(title=f"Next {len(events)} F1 Sessions", color=0xE10600)
        for name, when in format_event_details(events):
            embed.add_field(name=name, value=when, inline=False)
        await interaction.followup.send(embed=embed)

    @nextcord.slash_command(
        name="f1_countdown", description="Countdown to next F1 session"
    )
    async def f1_countdown(self, interaction: nextcord.Interaction):
        if GUILD_ID and interaction.guild and interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "Not available in this server.", ephemeral=True
            )
        await interaction.response.defer()
        events = await fetch_events(limit=1)
        if not events:
            return await interaction.followup.send("⚠️ No upcoming Grand Prix found.")
        dt, name = events[0]
        await interaction.followup.send(
            f"⏱ **{name}** starts in {format_countdown(dt)}"
        )

    @nextcord.slash_command(name="f1_results", description="Latest race results")
    async def f1_results(self, interaction: nextcord.Interaction):
        if GUILD_ID and interaction.guild and interaction.guild.id != GUILD_ID:
            return await interaction.response.send_message(
                "Not available in this server.", ephemeral=True
            )
        await interaction.response.defer()
        race_name, results = await fetch_race_results()
        if not race_name:
            return await interaction.followup.send("⚠️ Unable to fetch results.")
        embed = nextcord.Embed(title=f"{race_name} Results", color=0xE10600)
        for pos, driver, team in results:
            embed.add_field(name=f"#{pos} {driver}", value=team, inline=False)
        await interaction.followup.send(embed=embed)

    @nextcord.slash_command(
        name="f1_subscribe", description="DM reminders 10 min before each session"
    )
    async def f1_subscribe(self, interaction: nextcord.Interaction):
        self.subscribers.add(interaction.user.id)
        save_subscribers(self.subscribers)
        await interaction.response.send_message(
            "✅ You will receive session reminders.", ephemeral=True
        )

    @nextcord.slash_command(name="f1_unsubscribe", description="Stop session reminders")
    async def f1_unsubscribe(self, interaction: nextcord.Interaction):
        self.subscribers.discard(interaction.user.id)
        save_subscribers(self.subscribers)
        await interaction.response.send_message(
            "🛑 You have been unsubscribed.", ephemeral=True
        )


def setup(bot: commands.Bot):
    """Add the F1 cog and preload the schedule."""
    cog = F1Cog(bot)
    bot.add_cog(cog)
    logger.info("✅ Loaded F1Cog")
    bot.loop.create_task(cog.get_schedule())  # Preload schedule on startup
    logger.info("F1 schedule preloaded.")
