import nextcord
from nextcord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

class PingTest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="pingtest", description="Test slash command globally")
    async def pingtest(self, interaction: nextcord.Interaction):
        await interaction.response.send_message("🏓 Pong!")

    @commands.Cog.listener()
    async def on_ready(self):
        print("✅ [pingtest.py] Connected to guilds:")
        for guild in self.bot.guilds:
            print(f" - {guild.name} ({guild.id})")
        synced = await self.bot.sync_application_commands()
        print(f"✅ Synced {len(synced)} global slash commands")

def setup(bot):
    bot.add_cog(PingTest(bot))
    print("✅ Loaded PingTest cog")

