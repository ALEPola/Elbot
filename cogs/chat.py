# cogs/chat.py

import time
import asyncio
import logging
from collections import defaultdict, deque

import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from textblob import TextBlob
from openai import OpenAI

from elbot.config import Config

logger = logging.getLogger("elbot.chat")

# Initialize OpenAI client from Config
openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
OPENAI_MODEL = Config.OPENAI_MODEL
RATE_LIMIT = 5  # seconds between requests per user
# Maximum characters allowed in a response
MAX_RESPONSE_LENGTH = 2000
HISTORY_LEN = 5
HISTORY_TTL = 600  # seconds


class ChatCog(commands.Cog):
    """
    A cog for managing simple chat interactions via OpenAI.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_last_interaction = {}  # user_id → timestamp
        self.histories = defaultdict(deque)

    @nextcord.slash_command(
        name="chat", description="Chat with the bot (powered by OpenAI)."
    )
    async def chat(
        self,
        interaction: nextcord.Interaction,
        message: str = SlashOption(
            name="message", description="Your message to the bot", required=True
        ),
    ):
        """
        Respond to a user's message, with a per-user rate limit.
        """
        user_id = interaction.user.id
        now = time.monotonic()
        last = self.user_last_interaction.get(user_id, 0)
        if now - last < RATE_LIMIT:
            await interaction.response.send_message(
                "🕑 Please wait a few seconds before chatting again.", ephemeral=True
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

        history = self.histories[user_id]
        # Remove old history entries
        history = deque((h for h in history if now - h[0] <= HISTORY_TTL))
        self.histories[user_id] = history
        messages = [{"role": role, "content": msg} for _, role, msg in history]
        messages.append({"role": "user", "content": text})

        try:
            # Offload the blocking OpenAI chat completion call
            completion = await asyncio.to_thread(
                lambda: openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                )
            )
            content = completion.choices[0].message.content
            if len(content) > MAX_RESPONSE_LENGTH:
                content = content[: MAX_RESPONSE_LENGTH - 3] + "..."
        except Exception:
            logger.error("OpenAI error while generating response.", exc_info=True)
            content = "⚠️ Sorry, something went wrong with the chat bot."

        await interaction.followup.send(content)
        history.append((now, "user", text))
        history.append((now, "assistant", content))
        while len(history) > HISTORY_LEN * 2:
            history.popleft()

    @nextcord.slash_command(name="chat_reset", description="Clear chat history")
    async def chat_reset(self, interaction: nextcord.Interaction):
        self.histories.pop(interaction.user.id, None)
        await interaction.response.send_message(
            "✅ Chat history cleared.", ephemeral=True
        )


def setup(bot: commands.Bot):
    bot.add_cog(ChatCog(bot))
    logger.info("✅ Loaded ChatCog")
