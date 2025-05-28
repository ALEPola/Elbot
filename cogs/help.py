import nextcord
from nextcord.ext import commands
from nextcord.ui import View, Button
from nextcord.ext.commands import has_permissions

class HelpView(View):
    def __init__(self):
        super().__init__()
        self.add_item(Button(label="Music Commands", style=nextcord.ButtonStyle.primary, custom_id="music_help"))
        self.add_item(Button(label="F1 Commands", style=nextcord.ButtonStyle.primary, custom_id="f1_help"))
        self.add_item(Button(label="General Commands", style=nextcord.ButtonStyle.primary, custom_id="general_help"))

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="help", description="Get help with bot commands.")
    @has_permissions(administrator=True)
    async def help(self, interaction: nextcord.Interaction):
        """
        Provide help information, restricted to administrators.
        """
        embed = nextcord.Embed(
            title="Help Menu",
            description="List of available commands:",
            color=0x00ff00
        )
        embed.add_field(name="/play <song>", value="Play a song from YouTube.", inline=False)
        embed.add_field(name="/queue", value="View the current music queue.", inline=False)
        embed.add_field(name="/skip", value="Skip the current song.", inline=False)
        embed.add_field(name="/stop", value="Stop playback and clear the queue.", inline=False)
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: nextcord.Interaction):
        # Check if the interaction is a component interaction before accessing custom_id
        if interaction.type == nextcord.InteractionType.component:
            if interaction.custom_id == "music_help":
                embed = nextcord.Embed(
                    title="Music Commands",
                    description="List of music-related commands:",
                    color=0x00ff00
                )
                embed.add_field(name="/play <song>", value="Play a song from YouTube.", inline=False)
                embed.add_field(name="/queue", value="View the current music queue.", inline=False)
                embed.add_field(name="/skip", value="Skip the current song.", inline=False)
                await interaction.response.edit_message(embed=embed, view=HelpView())

            elif interaction.custom_id == "f1_help":
                embed = nextcord.Embed(
                    title="F1 Commands",
                    description="List of F1-related commands:",
                    color=0x00ff00
                )
                embed.add_field(name="/f1_schedule", value="View the upcoming F1 schedule.", inline=False)
                embed.add_field(name="/f1_reminders", value="Manage F1 race reminders.", inline=False)
                await interaction.response.edit_message(embed=embed, view=HelpView())

            elif interaction.custom_id == "general_help":
                embed = nextcord.Embed(
                    title="General Commands",
                    description="List of general bot commands:",
                    color=0x00ff00
                )
                embed.add_field(name="/ping", value="Check the bot's latency.", inline=False)
                embed.add_field(name="/help", value="Get help with bot commands.", inline=False)
                await interaction.response.edit_message(embed=embed, view=HelpView())

    @nextcord.slash_command(name="feedback", description="Submit feedback or report a bug.")
    async def feedback(self, interaction: nextcord.Interaction, *, message: str):
        """
        Collect feedback or bug reports from users.
        """
        feedback_channel = self.bot.get_channel(FEEDBACK_CHANNEL_ID)  # Replace with your feedback channel ID
        if feedback_channel:
            await feedback_channel.send(f"📢 Feedback from {interaction.user}: {message}")
            await interaction.response.send_message("✅ Thank you for your feedback!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Feedback channel not found. Please contact an admin.", ephemeral=True)

def setup(bot):
    bot.add_cog(HelpCog(bot))
