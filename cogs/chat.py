# cogs/chat.py

import time
import json
import asyncio
import logging
from pathlib import Path
from collections import defaultdict, deque

import nextcord
from nextcord.ext import commands
from nextcord import SlashOption
from textblob import TextBlob
from openai import OpenAI

from elbot.config import Config
from elbot.utils import safe_reply

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
        self.history_dir = Path(Config.BASE_DIR) / "chat_history"
        self.history_dir.mkdir(exist_ok=True)

    def _persist_history(self, user_id: int, role: str, content: str) -> None:
        file = self.history_dir / f"{user_id}.json"
        try:
            if file.exists():
                data = json.loads(file.read_text())
            else:
                data = []
        except Exception:
            data = []
        data.append({"ts": time.time(), "role": role, "content": content})
        with file.open("w") as f:
            json.dump(data, f)

    def _load_history(self, user_id: int) -> list:
        file = self.history_dir / f"{user_id}.json"
        if not file.exists():
            return []
        try:
            return json.loads(file.read_text())
        except Exception:
            return []

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
        await interaction.response.defer(thinking=True)
        user_id = interaction.user.id
        now = time.monotonic()
        last = self.user_last_interaction.get(user_id, 0)
        if now - last < RATE_LIMIT:
            await safe_reply(
                interaction,
                "🕑 Please wait a few seconds before chatting again.",
                ephemeral=True,
            )
            return
        self.user_last_interaction[user_id] = now

        text = message.strip()
        sentiment = TextBlob(text).sentiment
        logger.info(f"User {user_id} sentiment: {sentiment}")
        if sentiment.polarity < -0.5:
            await safe_reply(
                interaction,
                "It seems like you're upset. How can I help?",
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

        await safe_reply(interaction, content)
        history.append((now, "user", text))
        history.append((now, "assistant", content))
        self._persist_history(user_id, "user", text)
        self._persist_history(user_id, "assistant", content)
        while len(history) > HISTORY_LEN * 2:
            history.popleft()

    @nextcord.slash_command(name="chat_reset", description="Clear chat history")
    async def chat_reset(self, interaction: nextcord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        self.histories.pop(interaction.user.id, None)
        history_file = self.history_dir / f"{interaction.user.id}.json"
        try:
            history_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to remove chat history for %s: %s", interaction.user.id, exc)
        await safe_reply(interaction, "✅ Chat history cleared.", ephemeral=True)

    @nextcord.slash_command(name="chat_summary", description="Summarize recent chat")
    async def chat_summary(self, interaction: nextcord.Interaction):
        """Summarize the persisted conversation with the user."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        user_id = interaction.user.id
        history = self._load_history(user_id)
        if not history:
            await safe_reply(
                interaction, "No chat history found.", ephemeral=True
            )
            return
        conversation = "\n".join(f"{h['role']}: {h['content']}" for h in history[-20:])
        try:
            summary = await asyncio.to_thread(
                lambda: openai_client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "Summarize the following conversation."},
                        {"role": "user", "content": conversation},
                    ],
                )
            )
            content = summary.choices[0].message.content
        except Exception:
            logger.error("OpenAI error while summarizing.", exc_info=True)
            content = "⚠️ Failed to generate summary."
        await safe_reply(interaction, content, ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(ChatCog(bot))
    logger.info("✅ Loaded ChatCog")
