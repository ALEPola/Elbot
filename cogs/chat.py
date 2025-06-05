# cogs/chat.py

import time
import asyncio
import logging
import os

import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from textblob import TextBlob
from openai import OpenAI

from elbot.config import Config

logger = logging.getLogger("elbot.chat")

# Initialize OpenAI client from Config
openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
RATE_LIMIT = 5  # seconds between requests per user

class ChatCog(commands.Cog):
    """
    A cog for managing simple chat interactions via OpenAI.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_last_interaction = {}  # user_id → timestamp

    @nextcord.slash_command(
        name="chat",
        description="Chat with the bot (powered by OpenAI)."
    )
    async def chat(
        self,
        interaction: nextcord.Interaction,
        message: str = SlashOption(
            name="message",
            description="Your message to the bot",
            required=True
        )
    ):
        """
        Respond to a user's message, with a per-user rate limit.
        """
        user_id = interaction.user.id
        now = time.monotonic()
        last = self.user_last_interaction.get(user_id, 0)
        if now - last < RATE_LIMIT:
            await interaction.response.send_message(
                "🕑 Please wait a few seconds before chatting again.",
                ephemeral=True
            )
            return
        self.user_last_interaction[user_id] = now

        await interaction.response.defer()

        text = message.strip()
        sentiment = TextBlob(text).sentiment
        logger.info(f"User {user_id} sentiment: {sentiment}")
        if sentiment.polarity < -0.5:
            await interaction.followup.send(
                "It seems like you're upset. How can I help?"
            )
            return

        try:
            # Offload the blocking OpenAI call
            resp = await asyncio.to_thread(
                lambda: openai_client.responses.create(
                    model=OPENAI_MODEL,
                    input=text
                )
            )
            content = resp.output_text
        except Exception as e:
            logger.error("OpenAI error while generating response.", exc_info=True)
            content = "⚠️ Sorry, something went wrong with the chat bot."

        await interaction.followup.send(content)

def setup(bot: commands.Bot):
    bot.add_cog(ChatCog(bot))
    logger.info("✅ Loaded ChatCog")

