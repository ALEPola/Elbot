# cogs/f1.py

import nextcord
from nextcord.ext import commands, tasks
import aiohttp
from datetime import datetime, time
import pytz
import os
from dotenv import load_dotenv

load_dotenv()
GUILD_ID   = int(os.getenv("GUILD_ID",        "0"))
CHANNEL_ID = int(os.getenv("F1_CHANNEL_ID",    "0"))
UTC        = pytz.UTC

class F1Cog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start the weekly loop (runs at 12:00 UTC every day, but only posts on Sundays)
        self.weekly_update.start()

    def cog_unload(self):
        self.weekly_update.cancel()

    @tasks.loop(time=time(hour=12, tzinfo=UTC))
    async def weekly_update(self):
        # Only post on Sundays (weekday 6)
        now = datetime.now(UTC)
        if now.weekday() != 6:
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            return  # Channel not found / bot has no access

        # Fetch data
        results   = await self.fetch_last_race()        # list of result dicts
        next_race = await self.fetch_next_races(1)      # list of race dicts
        standings = await self.fetch_standings(5)       # list of standing dicts

        # Build embed
        embed = nextcord.Embed(
            title="🏁 Sunday F1 Briefing",
            color=0xE10600
        )
        embed.add_field(
            name="Last Race Results",
            value=self.format_results(results),
            inline=False
        )
        embed.add_field(
            name="Next Race",
            value=self.format_race(next_race[0]),
            inline=False
        )
        embed.add_field(
            name="Championship Top 5",
            value=self.format_standings(standings),
            inline=False
        )

        await channel.send(embed=embed)

    @weekly_update.before_loop
    async def before_weekly(self):
        await self.bot.wait_until_ready()

    # ---- Slash Commands ----

    @commands.slash_command(
        name="f1_update",
        guild_ids=[GUILD_ID],
        description="Force-post the F1 briefing now"
    )
    async def manual_update(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        await self.weekly_update()
        await interaction.followup.send("🔄 F1 briefing posted!")

    @commands.slash_command(
        name="f1_driver",
        guild_ids=[GUILD_ID],
        description="Get info on a driver"
    )
    async def driver_info(
        self,
        interaction: nextcord.Interaction,
        driver: str = nextcord.SlashOption(name="name", description="Driver surname")
    ):
        drivers = await self.fetch_driver(driver)
        embed   = self.build_driver_embed(drivers)
        await interaction.response.send_message(embed=embed)

    @commands.slash_command(
        name="f1_team",
        guild_ids=[GUILD_ID],
        description="Get info on a constructor/team"
    )
    async def team_info(
        self,
        interaction: nextcord.Interaction,
        team: str = nextcord.SlashOption(name="name", description="Team name")
    ):
        teams = await self.fetch_team(team)
        embed = self.build_team_embed(teams)
        await interaction.response.send_message(embed=embed)

    # ---- Data Fetching ----

    async def fetch_last_race(self):
        url = "https://ergast.com/api/f1/current/last/results.json"
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            data = await resp.json()
        # Returns a list of result dicts
        return data["MRData"]["RaceTable"]["Races"][0]["Results"]

    async def fetch_next_races(self, n=1):
        url = "https://ergast.com/api/f1/current.json?limit=1000"
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            data = await resp.json()
        races = data["MRData"]["RaceTable"]["Races"]
        now = datetime.utcnow().replace(tzinfo=UTC)
        upcoming = [
            r for r in races
            if datetime.fromisoformat(f"{r['date']}T{r['time']}+00:00") > now
        ]
        return upcoming[:n]

    async def fetch_standings(self, top=5):
        url = "https://ergast.com/api/f1/current/driverStandings.json"
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            data = await resp.json()
        standings = data["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]
        return standings[:top]

    async def fetch_driver(self, surname: str):
        url = f"https://ergast.com/api/f1/current/drivers.json?surname={surname}"
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            data = await resp.json()
        return data["MRData"]["DriverTable"]["Drivers"]

    async def fetch_team(self, name: str):
        url = f"https://ergast.com/api/f1/current/constructors.json?constructorId={name.lower()}"
        async with aiohttp.ClientSession() as sess:
            resp = await sess.get(url)
            data = await resp.json()
        return data["MRData"]["ConstructorTable"]["Constructors"]

    # ---- Formatters ----

    def format_results(self, results: list) -> str:
        podium = results[:3]
        return "\n".join(
            f"{i+1}. {r['Driver']['givenName']} {r['Driver']['familyName']} "
            f"({r['Constructor']['name']})"
            for i, r in enumerate(podium)
        )

    def format_race(self, race: dict) -> str:
        dt = datetime.fromisoformat(f"{race['date']}T{race['time']}+00:00") \
                    .astimezone(UTC)
        return f"**{race['raceName']}** — {dt.strftime('%A, %b %d %I:%M %p UTC')}"

    def format_standings(self, standings: list) -> str:
        return "\n".join(
            f"{i+1}. {d['Driver']['familyName']} — {d['points']} pts"
            for i, d in enumerate(standings)
        )

    # ---- Embeds for Driver/Team ----

    def build_driver_embed(self, drivers: list) -> nextcord.Embed:
        embed = nextcord.Embed(title="Driver Info", color=0x1F8B4C)
        if not drivers:
            embed.description = "No driver found."
        else:
            d = drivers[0]
            embed.add_field("Name", f"{d['givenName']} {d['familyName']}", inline=True)
            embed.add_field("Nationality", d["nationality"], inline=True)
            embed.add_field("DOB", d["dateOfBirth"], inline=True)
        return embed

    def build_team_embed(self, teams: list) -> nextcord.Embed:
        embed = nextcord.Embed(title="Constructor Info", color=0x0055A4)
        if not teams:
            embed.description = "No team found."
        else:
            t = teams[0]
            embed.add_field("Name", t["name"], inline=True)
            embed.add_field("Nationality", t["nationality"], inline=True)
        return embed

def setup(bot: commands.Bot):
    bot.add_cog(F1Cog(bot))
    print("F1Cog loaded")
    # Note: This function is called by the bot when loading the cog.


