import os
import asyncio
import logging

import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from textblob import TextBlob
from dotenv import load_dotenv
import openai
import time

# Load .env
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Config
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MAX_TOKENS= int(os.getenv("OPENAI_MAX_TOKENS", "200"))
RATE_LIMIT       = 5  # seconds
GUILD_ID         = int(os.getenv("GUILD_ID", "0"))

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")
openai.api_key = OPENAI_API_KEY

# In‑memory state
user_chat_histories    = {}
user_last_interaction  = {}


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(
        name="chat",
        description="Chat with the bot using OpenAI.",
        guild_ids=[GUILD_ID]  # ← register in your server instantly
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

        # Rate‑limit
        now = time.monotonic()
        last = user_last_interaction.get(user_id, 0)
        if now - last < RATE_LIMIT:
            return await interaction.response.send_message(
                "🕑 Please wait a moment before chatting again.",
                ephemeral=True
            )
        user_last_interaction[user_id] = now

        await interaction.response.defer()

        text = message.strip()
        sentiment = TextBlob(text).sentiment
        logger.info(f"User {user_id} sentiment: {sentiment}")
        if sentiment.polarity < -0.5:
            return await interaction.followup.send(
                "It seems like you're upset. How can I help?"
            )

        reply = await self.generate_response(user_id, text)
        await interaction.followup.send(reply)

    async def generate_response(self, user_id: int, message: str) -> str:
        history = user_chat_histories.setdefault(user_id, [])
        history.append({"role": "user", "content": message})
        # keep last 30
        user_chat_histories[user_id] = history[-30:]

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: openai.ChatCompletion.create(
                    model=OPENAI_MODEL,
                    messages=user_chat_histories[user_id],
                    temperature=0.7,
                    max_tokens=OPENAI_MAX_TOKENS
                )
            )
            content = resp.choices[0].message.content
            user_chat_histories[user_id].append(
                {"role": "assistant", "content": content}
            )
            return content

        except Exception as e:
            logger.error("OpenAI error:", exc_info=True)
            return "⚠️ Oops, something went wrong with the AI."

def setup(bot: commands.Bot):
    bot.add_cog(ChatCog(bot))
    logger.info("✅ Loaded ChatCog")










