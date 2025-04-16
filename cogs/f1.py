import nextcord
from nextcord.ext import commands, tasks
import aiohttp
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
CHANNEL_ID = 1360675364271296674

class FormulaOne(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_race_round = None
        self.check_race_updates.start()

    @nextcord.slash_command(
        name="f1_next",
        description="Show next 3 Formula 1 races",
        guild_ids=[GUILD_ID]
    )
    async def f1_next(self, interaction: nextcord.Interaction):
        upcoming = await self.get_upcoming_races(3)
        embed = nextcord.Embed(title="🏎️ Upcoming F1 Races", color=nextcord.Color.red())
        for race in upcoming:
            name = f"{race['raceName']} ({race['Circuit']['circuitName']})"
            date = self.parse_datetime(race['date'], race['time'])
            embed.add_field(
                name=name,
                value=f"🗓 {date.strftime('%A, %B %d at %I:%M %p')} UTC",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    async def get_upcoming_races(self, count=3):
        url = "https://ergast.com/api/f1/current.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        races = data['MRData']['RaceTable']['Races']
        # Make 'now' timezone-aware
        now = datetime.utcnow().replace(tzinfo=pytz.UTC)
        upcoming = [r for r in races if self.parse_datetime(r['date'], r['time']) > now]
        return upcoming[:count]

    def parse_datetime(self, date_str, time_str):
        # Ensure the time string ends with "Z" (UTC) and then build an ISO string with an explicit offset.
        # If not, append "+00:00" to ensure the result is aware.
        if not time_str.endswith("Z"):
            time_str += "Z"
        # Remove the trailing "Z" and add an explicit UTC offset.
        full_dt = f"{date_str}T{time_str[:-1]}+00:00"
        return datetime.fromisoformat(full_dt)

    async def get_last_race_results(self):
        url = "https://ergast.com/api/f1/current/last/results.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        return data['MRData']['RaceTable']['Races'][0]

    async def get_standings(self):
        url = "https://ergast.com/api/f1/current/driverStandings.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        return data['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']

    @tasks.loop(minutes=30)
    async def check_race_updates(self):
        try:
            data = await self.get_last_race_results()
            # Always update with the latest data regardless of the round number
            round_no = int(data["round"])
            self.last_race_round = round_no  # Optionally store this for future reference

            channel = self.bot.get_channel(CHANNEL_ID)
            if channel is None:
                logger.error("Channel not found for ID: %s", CHANNEL_ID)
                return

            race_name = data['raceName']
            results = data['Results'][:3]
            podium = "\n".join([
                f"{i+1}. {r['Driver']['givenName']} {r['Driver']['familyName']} ({r['Constructor']['name']})"
                for i, r in enumerate(results)
            ])
            result_message = f"🏎️ **{race_name} Results:**\n{podium}"

            standings = (await self.get_standings())[:5]
            leaderboard = "\n".join([
                f"{i+1}. {d['Driver']['familyName']} - {d['points']} pts"
                for i, d in enumerate(standings)
            ])
            standings_message = f"📊 **Championship Standings:**\n{leaderboard}"

            # Send a combined message with the latest race results and standings
            full_message = f"{result_message}\n\n{standings_message}"
            await channel.send(full_message)

        except Exception as e:
            logger.error("Error in F1 update loop: %s", e)

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            synced = await self.bot.sync_application_commands(guild_id=GUILD_ID)
            if synced is None:
                logger.info("✅ No commands were synced for guild %s", GUILD_ID)
            else:
                logger.info("✅ Synced %s slash commands to guild %s", len(synced), GUILD_ID)
        except Exception as e:
            logger.error("❌ Failed to sync guild commands: %s", e)

def setup(bot):
    bot.add_cog(FormulaOne(bot))
    print("✅ Loaded FormulaOne cog")

