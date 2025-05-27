import nextcord
from nextcord.ext import commands

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="help", description="Get help with bot commands.")
    async def help(self, interaction: nextcord.Interaction):
        embed = nextcord.Embed(title="Help Menu", description="List of available commands:", color=0x00ff00)
        embed.add_field(name="/play <song>", value="Play a song from YouTube. Example: /play Never Gonna Give You Up", inline=False)
        embed.add_field(name="/queue", value="View the current music queue.", inline=False)
        embed.add_field(name="/skip", value="Skip the current song.", inline=False)
        embed.add_field(name="/stop", value="Stop playback and clear the queue.", inline=False)
        await interaction.response.send_message(embed=embed)

def setup(bot):
    bot.add_cog(HelpCog(bot))
