# cogs/f1.py

# cogs/f1_scrape.py

import aiohttp
from bs4 import BeautifulSoup
import nextcord
from nextcord.ext import commands, tasks
from datetime import datetime, time
from zoneinfo import ZoneInfo

# — Hardcoded from your .env —
GUILD_ID   = 761070952674230292
CHANNEL_ID = 951059416466214932

# Eastern Time (will handle EST/EDT automatically)
LOCAL_TZ = ZoneInfo("America/New_York")

async def get_next_race_from_official():
    year = datetime.now(LOCAL_TZ).year
    url = f"https://www.formula1.com/en/racing/{year}.html"
    async with aiohttp.ClientSession() as sess:
        resp = await sess.get(url)
        html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table.race-listing tbody tr")
    now = datetime.now(LOCAL_TZ)

    for tr in rows:
        date_cell = tr.select_one("td.date")
        name_cell = tr.select_one("td.event a")
        if not (date_cell and name_cell):
            continue

        month_day = date_cell.get_text(strip=True)  # e.g. "Apr 20"
        try:
            # parse as local date at midnight
            dt = datetime.strptime(f"{month_day} {year}", "%b %d %Y")
            dt = dt.replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue

        if dt > now:
            return {
                "name":      name_cell.get_text(strip=True),
                "datetime":  dt.strftime("%A, %b %d %I:%M %p %Z")
            }

    return None

class ScrapeF1Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weekly_update.start()

    @tasks.loop(time=time(hour=12, tzinfo=LOCAL_TZ))
    async def weekly_update(self):
        if datetime.now(LOCAL_TZ).weekday() != 6:  # only Sundays
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            return

        race = await get_next_race_from_official()
        if race:
            await channel.send(
                f"🏁 **Next F1 Race:** {race['name']}\n"
                f"📅 **When:** {race['datetime']}"
            )
        else:
            await channel.send("⚠️ Couldn’t find the next race on the official site.")

    @weekly_update.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    @commands.slash_command(
        name="f1_next",
        description="Get the next F1 race from the official site",
        guild_ids=[GUILD_ID]
    )
    async def f1_next(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        race = await get_next_race_from_official()
        if race:
            await interaction.followup.send(
                f"🏁 **Next F1 Race:** {race['name']}\n"
                f"📅 **When:** {race['datetime']}"
            )
        else:
            await interaction.followup.send("⚠️ Couldn’t find the next race on the official site.")

def setup(bot):
    bot.add_cog(ScrapeF1Cog(bot))
    print("✅ Loaded ScrapeF1Cog (using EST)")



