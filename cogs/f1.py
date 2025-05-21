# cogs/f1.py

import aiohttp
from icalendar import Calendar
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

GUILD_ID    = 761070952674230292
CHANNEL_ID  = 951059416466214932
LOCAL_TZ    = ZoneInfo("America/New_York")
ICS_URL     = "https://ics.ecal.com/ecal-sub/6803ebf123ccd40008c9e980/Formula%201.ics"

# In‑memory subscriber list (persist this to disk for real bots)
subscribers = set()

async def get_upcoming_events(limit=5):
    """Return a sorted list of (dt, summary) for the next `limit` Grand Prix events."""
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
    """Return a dd:hh:mm string until dt."""
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
    def __init__(self, bot):
        self.bot = bot
        self.weekly_update.start()
        self.reminder_loop.start()

    @tasks.loop(time=time(hour=12))
    async def weekly_update(self):
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
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def reminder_loop(self):
        """Every 30m check if any race is ~1 h away, DM subs."""
        events = await get_upcoming_events(limit=3)
        now = datetime.now(LOCAL_TZ)
        for dt, name in events:
            if 0 < (dt - now) <= timedelta(hours=1):
                message = f"⏰ Reminder: **{name}** starts in ~1h at {dt.strftime('%I:%M %p %Z')}"
                await notify_subscribers(self.bot, subscribers, message)

    @reminder_loop.before_loop
    async def before_reminders(self):
        await self.bot.wait_until_ready()

    @nextcord.slash_command(
        name="f1_schedule",
        description="Show the next N F1 races",
        guild_ids=[GUILD_ID]
    )
    async def f1_schedule(self, interaction: nextcord.Interaction,
                         count: int = nextcord.SlashOption(name="count", description="How many races?", required=False, default=5)):
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
        await interaction.response.defer()
        events = await get_upcoming_events(1)
        if not events:
            return await interaction.followup.send("⚠️ No upcoming Grand Prix found.")
        dt, name = events[0]
        countdown = format_countdown(dt)
        await interaction.followup.send(f"⏱ **{name}** starts in {countdown}")

    @nextcord.slash_command(
        name="f1_subscribe",
        description="Subscribe to 1h‑before race reminders",
        guild_ids=[GUILD_ID]
    )
    async def f1_subscribe(self, interaction: nextcord.Interaction):
        subscribers.add(interaction.user.id)
        await interaction.response.send_message("✅ You’ll get race reminders!", ephemeral=True)

    @nextcord.slash_command(
        name="f1_unsubscribe",
        description="Stop race reminders",
        guild_ids=[GUILD_ID]
    )
    async def f1_unsubscribe(self, interaction: nextcord.Interaction):
        subscribers.discard(interaction.user.id)
        await interaction.response.send_message("🛑 You’ve been unsubscribed.", ephemeral=True)

def setup(bot):
    bot.add_cog(F1Cog(bot))
    print("✅ Loaded F1Cog (extended with schedule, countdown & reminders)")






