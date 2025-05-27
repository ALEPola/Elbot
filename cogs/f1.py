"""
F1Cog: A cog for managing Formula 1-related commands and reminders.

This cog provides commands to display F1 schedules, countdowns, and manage race reminders.
"""

# cogs/f1.py

import aiohttp
from icalendar import Calendar
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import json
import os
from dotenv import load_dotenv

load_dotenv()

GUILD_ID    = int(os.getenv("GUILD_ID", "0"))
CHANNEL_ID  = int(os.getenv("CHANNEL_ID", "0"))
ICS_URL     = os.getenv("ICS_URL", "")
SUBSCRIBERS_FILE = "subscribers.json"

if not GUILD_ID or not CHANNEL_ID or not ICS_URL:
    raise RuntimeError("Missing required environment variables: GUILD_ID, CHANNEL_ID, or ICS_URL.")

# Define the local timezone
LOCAL_TZ = ZoneInfo("America/New_York")

# In‑memory subscriber list (persist this to disk for real bots)
# Load subscribers from file
try:
    with open(SUBSCRIBERS_FILE, "r") as f:
        subscribers = set(json.load(f))
except (FileNotFoundError, json.JSONDecodeError):
    subscribers = set()

# Save subscribers to file
def save_subscribers():
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(list(subscribers), f)

async def get_upcoming_events(limit=5):
    """
    Fetch and return a sorted list of upcoming Formula 1 events.

    Args:
        limit (int): The maximum number of events to return.

    Returns:
        list: A list of tuples containing the event datetime and summary.
    """
    async with aiohttp.ClientSession() as sess:
        resp = await sess.get(ICS_URL)
        cal  = Calendar.from_ical(await resp.text())

    now      = datetime.now(LOCAL_TZ)
    upcoming = []
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        summary = str(comp.get("SUMMARY", "")).lower()
        if "grand prix" not in summary:
            continue
        dt = comp.get("DTSTART").dt
        if isinstance(dt, datetime):
            dt = dt.astimezone(LOCAL_TZ)
        else:
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=LOCAL_TZ)
        if dt > now:
            upcoming.append((dt, comp.get("SUMMARY")))

    upcoming.sort(key=lambda x: x[0])
    return upcoming[:limit]

def format_countdown(dt):
    """
    Format a countdown string until the given datetime.

    Args:
        dt (datetime): The target datetime.

    Returns:
        str: A formatted string in the format 'dd:hh:mm'.
    """
    delta = dt - datetime.now(LOCAL_TZ)
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h {minutes}m"

# Helper function to format event details
def format_event_details(events):
    """Format a list of events into a string or embed fields."""
    formatted_events = []
    for dt, name in events:
        formatted_events.append((name, dt.strftime("%A, %b %d %I:%M %p %Z")))
    return formatted_events

# Helper function to notify subscribers
async def notify_subscribers(bot, subscribers, message):
    """Send a message to all subscribers."""
    for user_id in list(subscribers):
        user = await bot.fetch_user(user_id)
        await user.send(message)

class F1Cog(commands.Cog):
    """
    A cog for managing Formula 1-related commands and reminders.

    Attributes:
        bot (commands.Bot): The bot instance.
        start_time (datetime): The time when the cog was initialized.
    """

    def __init__(self, bot):
        """
        Initialize the F1Cog.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.weekly_update.start()
        self.reminder_loop.start()

    @tasks.loop(time=time(hour=12))
    async def weekly_update(self):
        """
        Send a weekly update about the next F1 race to the designated channel.

        This task runs every Sunday at 12 PM.
        """
        # only Sundays
        if datetime.now(LOCAL_TZ).weekday() != 6:
            return
        ch = self.bot.get_channel(CHANNEL_ID)
        events = await get_upcoming_events(1)
        if not events:
            await ch.send("⚠️ No upcoming Grand Prix found.")
            return
        dt, name = events[0]
        await ch.send(f"🏁 **Next F1 Race:** {name}\n📅 **When:** {dt.strftime('%A, %b %d %I:%M %p %Z')}")

    @weekly_update.before_loop
    async def before_weekly(self):
        """
        Wait until the bot is ready before starting the weekly update task.
        """
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def reminder_loop(self):
        """
        Check every 30 minutes if any race is ~1 hour away and DM subscribers.
        """
        events = await get_upcoming_events(limit=3)
        now = datetime.now(LOCAL_TZ)
        for dt, name in events:
            if timedelta(seconds=0) < (dt - now) <= timedelta(hours=1):
                message = f"⏰ Reminder: **{name}** starts in ~1h at {dt.strftime('%I:%M %p %Z')}"
                await notify_subscribers(self.bot, subscribers, message)

    @reminder_loop.before_loop
    async def before_reminders(self):
        """
        Wait until the bot is ready before starting the reminder loop.
        """
        await self.bot.wait_until_ready()

    @nextcord.slash_command(
        name="f1_schedule",
        description="Show the next N F1 races",
        guild_ids=[GUILD_ID]
    )
    async def f1_schedule(self, interaction: nextcord.Interaction, count: int = 5):
        """
        Display the schedule for the next N Formula 1 races.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            count (int): The number of races to display (default is 5).
        """
        await interaction.response.defer()
        events = await get_upcoming_events(count)
        embed = nextcord.Embed(title=f"Next {len(events)} Grands Prix", color=0xE10600)
        for name, details in format_event_details(events):
            embed.add_field(name=name, value=details, inline=False)
        await interaction.followup.send(embed=embed)

    @nextcord.slash_command(
        name="f1_countdown",
        description="Show a countdown until the next F1 race",
        guild_ids=[GUILD_ID]
    )
    async def f1_countdown(self, interaction: nextcord.Interaction):
        """
        Display a countdown until the next Formula 1 race.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        await interaction.response.defer()
        events = await get_upcoming_events(1)
        if not events:
            return await interaction.followup.send("⚠️ No upcoming Grand Prix found.")
        dt, name = events[0]
        countdown = format_countdown(dt)
        await interaction.followup.send(f"⏱ **{name}** starts in {countdown}")

    @nextcord.slash_command(
        name="f1_subscribe",
        description="Subscribe to 1h-before race reminders",
        guild_ids=[GUILD_ID]
    )
    async def f1_subscribe(self, interaction: nextcord.Interaction):
        """
        Subscribe the user to 1-hour-before race reminders.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        subscribers.add(interaction.user.id)
        save_subscribers()
        await interaction.response.send_message("✅ You’ll get race reminders!", ephemeral=True)

    @nextcord.slash_command(
        name="f1_unsubscribe",
        description="Stop race reminders",
        guild_ids=[GUILD_ID]
    )
    async def f1_unsubscribe(self, interaction: nextcord.Interaction):
        """
        Unsubscribe the user from race reminders.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        subscribers.discard(interaction.user.id)
        save_subscribers()
        await interaction.response.send_message("🛑 You’ve been unsubscribed.", ephemeral=True)

def setup(bot):
    """
    Set up the F1Cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    bot.add_cog(F1Cog(bot))
    print("✅ Loaded F1Cog (extended with schedule, countdown & reminders)")






