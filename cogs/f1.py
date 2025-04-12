import nextcord
from nextcord.ext import commands, tasks
import requests
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv

load_dotenv()
CHANNEL_ID = 1360675364271296674
GUILD_ID = int(os.getenv("GUILD_ID"))  # Ensure your .env has a correct GUILD_ID value

class FormulaOne(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_race_round = None
        self.check_race_updates.start()

    @nextcord.slash_command(
        name="f1_next",
        description="Show next 3 Formula 1 races",
        guild_ids=[GUILD_ID]  # Remove guild_ids for global commands (which can take longer to propagate)
    )
    async def f1_next(self, interaction: nextcord.Interaction):
        upcoming = self.get_upcoming_races(3)
        embed = nextcord.Embed(title="🏎️ Upcoming F1 Races", color=nextcord.Color.red())
        for race in upcoming:
            name = f"{race['raceName']} ({race['Circuit']['circuitName']})"
            date = self.parse_datetime(race['date'], race['time'])
            embed.add_field(name=name, value=f"🗓 {date.strftime('%A, %B %d at %I:%M %p')} UTC", inline=False)
        await interaction.response.send_message(embed=embed)

    def get_upcoming_races(self, count=3):
        res = requests.get("https://ergast.com/api/f1/current.json")
        races = res.json()['MRData']['RaceTable']['Races']
        now = datetime.utcnow()
        return [r for r in races if self.parse_datetime(r['date'], r['time']) > now][:count]

    def parse_datetime(self, date_str, time_str):
        full_dt = f"{date_str}T{time_str.replace('Z', '')}"
        dt = datetime.fromisoformat(full_dt)
        return dt.replace(tzinfo=pytz.UTC)

    def get_last_race_results(self):
        res = requests.get("https://ergast.com/api/f1/current/last/results.json")
        data = res.json()['MRData']['RaceTable']['Races'][0]
        return data

    def get_standings(self):
        res = requests.get("https://ergast.com/api/f1/current/driverStandings.json")
        return res.json()['MRData']['StandingsTable']['StandingsLists'][0]['DriverStandings']

    @tasks.loop(minutes=30)
    async def check_race_updates(self):
        try:
            data = self.get_last_race_results()
            round_no = int(data["round"])
            if self.last_race_round == round_no:
                return
            self.last_race_round = round_no

            channel = self.bot.get_channel(CHANNEL_ID)
            race_name = data['raceName']
            results = data['Results'][:3]
            podium = "\n".join([
                f"{i+1}. {r['Driver']['givenName']} {r['Driver']['familyName']} ({r['Constructor']['name']})"
                for i, r in enumerate(results)
            ])
            await channel.send(f"🏎️ **{race_name}** Results:\n{podium}")

            standings = self.get_standings()[:5]
            leaderboard = "\n".join([
                f"{i+1}. {d['Driver']['familyName']} - {d['points']} pts"
                for i, d in enumerate(standings)
            ])
            await channel.send(f"📊 **Championship Standings:**\n{leaderboard}")

        except Exception as e:
            print(f"Error in F1 update loop: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            # Optional: You can sync commands here as well to ensure this cog's commands are registered.
            await self.bot.sync_application_commands()
            print("✅ Synced slash commands from f1.py")
        except Exception as e:
            print(f"❌ Failed to sync commands from f1.py: {e}")

def setup(bot):
    bot.add_cog(FormulaOne(bot))
    print("✅ Loaded FormulaOne cog")   