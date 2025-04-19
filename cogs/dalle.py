import nextcord
from nextcord.ext import commands
import openai
import os
from dotenv import load_dotenv
import asyncio

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
GUILD_ID = int(os.getenv('GUILD_ID'))  # Make sure to load and convert to an integer

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable not set.")
if not GUILD_ID:
    raise ValueError("GUILD_ID environment variable not set.")

openai.api_key = OPENAI_API_KEY

class ImageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="dalle", description="Generate an image using DALL·E", guild_ids=[GUILD_ID])
    async def generate_image(self, interaction: nextcord.Interaction, prompt: str):
        await interaction.response.defer()  # To show loading

        try:
            response = await asyncio.to_thread(
                openai.images.generate,
                model="dall-e-3",  # DALL·E 3 model
                prompt=prompt,
                size="1024x1024",  # Best size for square image
                quality="hd",  # Best quality setting
                n=1
            )

            image_url = response.data[0].url
            embed = nextcord.Embed(title="Here is your generated image:")
            embed.set_image(url=image_url)  # Embed the image
            await interaction.followup.send(embed=embed)  # Send the embed with image

        except openai.OpenAIError as e:
            # Handle the content policy violation more gracefully
            if 'content_policy_violation' in str(e):
                await interaction.followup.send("The prompt you used violates the content policy. Please try again with a different prompt.")
            else:
                await interaction.followup.send(f"An error occurred: {e}")

def setup(bot):
    bot.add_cog(ImageCog(bot))









