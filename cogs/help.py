import nextcord
from nextcord.ext import commands

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="help", description="Show help and command usage.")
    async def help(self, interaction: nextcord.Interaction):
        embed = nextcord.Embed(title="Bot Help", description="List of available commands:", color=nextcord.Color.blurple())
        embed.add_field(name="/play", value="Play a song from YouTube. Usage: `/play [song name or URL]`", inline=False)
        embed.add_field(name="/queue_details", value="View detailed queue information.", inline=False)
        embed.add_field(name="/remove_track", value="Remove a track from the queue by its position.", inline=False)
        embed.add_field(name="/move_track", value="Move a track to a new position in the queue.", inline=False)
        embed.add_field(name="/search", value="Search for a track and select one to queue.", inline=False)
        embed.add_field(name="/volume", value="Adjust playback volume (0-150%).", inline=False)
        embed.add_field(name="/help", value="Show this help message.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

def setup(bot):
    bot.add_cog(HelpCog(bot))
