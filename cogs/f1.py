# cogs/f1.py

import aiohttp
from icalendar import Calendar
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, time
from zoneinfo import ZoneInfo

GUILD_ID   = 761070952674230292
CHANNEL_ID = 951059416466214932
LOCAL_TZ   = ZoneInfo("America/New_York")
ICS_URL    = "https://ics.ecal.com/ecal-sub/6803ebf123ccd40008c9e980/Formula%201.ics"

async def get_next_race_from_ics():
    async with aiohttp.ClientSession() as sess:
        resp = await sess.get(ICS_URL)
        cal = Calendar.from_ical(await resp.text())

    now = datetime.now(LOCAL_TZ)
    upcoming = []
    for comp in cal.walk():
        if comp.name != "VEVENT": continue
        dt = comp.get("DTSTART").dt
        if not isinstance(dt, datetime):
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=LOCAL_TZ)
        else:
            dt = dt.astimezone(LOCAL_TZ)
        if dt > now:
            upcoming.append((dt, str(comp.get("SUMMARY"))))

    if not upcoming:
        return None
    dt, name = sorted(upcoming, key=lambda x: x[0])[0]
    return {"name": name, "datetime": dt.strftime("%A, %b %d %I:%M %p %Z")}

class F1Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weekly_update.start()

    # ← schedule at local 12:00, no tzinfo argument
    @tasks.loop(time=time(hour=12))
    async def weekly_update(self):
        now = datetime.now(LOCAL_TZ)
        if now.weekday() != 6:
            return

        ch = self.bot.get_channel(CHANNEL_ID)
        if not ch:
            return

        race = await get_next_race_from_ics()
        if race:
            await ch.send(
                f"🏁 **Next F1 Race:** {race['name']}\n"
                f"📅 **When:** {race['datetime']}"
            )
        else:
            await ch.send("⚠️ Couldn’t find the next race in the ICS feed.")

    @weekly_update.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    @nextcord.slash_command(
        name="f1_next",
        description="Get the next F1 race from the ICS calendar",
        guild_ids=[GUILD_ID]
    )
    async def f1_next(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        race = await get_next_race_from_ics()
        if race:
            await interaction.followup.send(
                f"🏁 **Next F1 Race:** {race['name']}\n"
                f"📅 **When:** {race['datetime']}"
            )
        else:
            await interaction.followup.send("⚠️ Couldn’t find the next race in the ICS feed.")

def setup(bot):
    bot.add_cog(F1Cog(bot))
    print("✅ Loaded F1Cog (ICS calendar)")





