import nextcord
from nextcord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()
GUILD_ID = int(os.getenv("GUILD_ID"))

class PingTest(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="pingtest", description="Test slash command", guild_ids=[GUILD_ID])
    async def pingtest(self, interaction: nextcord.Interaction):
        await interaction.response.send_message("🏓 Pong!")

    @commands.Cog.listener()
    async def on_ready(self):
        synced = await self.bot.sync_application_commands(guild_id=GUILD_ID)
        print(f"✅ Synced {len(synced)} commands to GUILD_ID in pingtest")

def setup(bot):
    bot.add_cog(PingTest(bot))
