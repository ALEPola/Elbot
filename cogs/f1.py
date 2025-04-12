import nextcord
from nextcord.ext import commands, tasks
import requests
from datetime import datetime
import pytz

CHANNEL_ID = 1360675364271296674  # F1 updates go here

class FormulaOne(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_race_round = None
        self.check_race_updates.start()

    @nextcord.slash_command(name="f1_next", description="Show next 3 Formula 1 races")
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
        full_dt = f"{date_str}T{time_str.replace('Z', '')}"  # e.g. 2025-04-14T14:00:00
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
                return  # Already posted
            self.last_race_round = round_no

            channel = self.bot.get_channel(CHANNEL_ID)
            race_name = data['raceName']
            results = data['Results'][:3]  # Top 3
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


def setup(bot):
    bot.add_cog(FormulaOne(bot))