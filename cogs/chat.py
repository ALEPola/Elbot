import os
import logging
import time

import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from textblob import TextBlob
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
RATE_LIMIT = 5
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

client = OpenAI(api_key=OPENAI_API_KEY)
user_last_interaction = {}

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(
        name="chat",
        description="Chat with the bot using OpenAI Responses API.",
        guild_ids=[GUILD_ID]
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
        user_id = interaction.user.id
        now = time.monotonic()
        last = user_last_interaction.get(user_id, 0)
        if now - last < RATE_LIMIT:
            await interaction.response.send_message(
                "🕑 Please wait a moment before chatting again.",
                ephemeral=True
            )
            return
        user_last_interaction[user_id] = now
        await interaction.response.defer()
        text = message.strip()
        sentiment = TextBlob(text).sentiment
        logger.info(f"User {user_id} sentiment: {sentiment}")
        if sentiment.polarity < -0.5:
            await interaction.followup.send("It seems like you're upset. How can I help?")
            return
        try:
            resp = await client.responses.acreate(
                model=OPENAI_MODEL,
                input=text
            )
            content = resp.output_text
        except Exception:
            logger.error("OpenAI error while creating response.", exc_info=True)
            content = "⚠️ Oops, something went wrong with the AI."
        await interaction.followup.send(content)

    def setup(bot: commands.Bot):
        bot.add_cog(ChatCog(bot))
        logger.info("✅ Loaded ChatCog")











