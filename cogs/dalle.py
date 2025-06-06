# cogs/dalle.py

import asyncio
import nextcord
from nextcord.ext import commands
import openai

from elbot.config import Config  # Use our central config

# Make sure OpenAI key is set in Config (Config.validate() will have caught a missing token).
openai.api_key = (
    Config.OPENAI_API_KEY
)  # You should add OPENAI_API_KEY to Config in config.py


class ImageCog(commands.Cog):
    """
    A cog that generates images with DALL·E via OpenAI.
    Expects OPENAI_API_KEY to be set in environment (loaded by Config).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(
        name="dalle",
        description="Generate an image using DALL·E 3",
        # No `guild_ids` here—command is globally registered. If you want to restrict to a single guild,
        # you can check `if interaction.guild.id != Config.GUILD_ID` at runtime and reject it.
    )
    async def generate_image(self, interaction: nextcord.Interaction, prompt: str):
        await interaction.response.defer()

        # Optional: if you really want to restrict to one guild:
        if (
            Config.GUILD_ID
            and interaction.guild
            and interaction.guild.id != Config.GUILD_ID
        ):
            await interaction.followup.send(
                "This command is not available in this server.", ephemeral=True
            )
            return

        try:
            # Offload to a thread since openai.images.generate is blocking
            response = await asyncio.to_thread(
                openai.images.generate,
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                n=1,
            )

            image_url = response.data[0].url
            embed = nextcord.Embed(title="Here’s your generated image:")
            embed.set_image(url=image_url)
            await interaction.followup.send(embed=embed)

        except openai.OpenAIError as e:
            # Handle content policy or other errors
            msg = str(e).lower()
            if "content_policy_violation" in msg:
                await interaction.followup.send(
                    "The prompt you used violates the content policy. Please try a different prompt.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"An error occurred: {e}", ephemeral=True
                )


def setup(bot: commands.Bot):
    bot.add_cog(ImageCog(bot))
