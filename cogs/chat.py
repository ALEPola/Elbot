import nextcord
from nextcord.ext import commands
import openai
import os
import logging
import asyncio
from textblob import TextBlob
from dotenv import load_dotenv
import time

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# In-memory data structures to store user data
user_chat_histories = {}
user_last_interaction = {}

# Configurable parameters
RATE_LIMIT_SECONDS = 5
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o")
MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "200"))

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def preprocess_text(self, text: str) -> str:
        """Preprocess input text."""
        return text.strip()

    @nextcord.slash_command(name="chat", description="Chat with the bot using OpenAI.")
    async def chat(self, interaction: nextcord.Interaction, message: str):
        user_id = interaction.user.id

        # Check rate limit
        if self.is_rate_limited(user_id):
            await interaction.response.send_message("You're asking too frequently, please wait a moment.", ephemeral=True)
            return

        await interaction.response.defer()

        message_preprocessed = self.preprocess_text(message)

        # Sentiment analysis with TextBlob
        sentiment = TextBlob(message_preprocessed).sentiment
        logger.info(f"User {user_id} sentiment analysis: {sentiment}")

        if sentiment.polarity < -0.5:
            await interaction.followup.send("It seems like you're upset. How can I help?")
            return

        response_text = await self.generate_openai_response(user_id, message_preprocessed)
        await interaction.followup.send(response_text)

    async def generate_openai_response(self, user_id: int, message: str) -> str:
        try:
            # Maintain user chat history for context
            history = user_chat_histories.setdefault(user_id, [])
            history.append({"role": "user", "content": message})
            user_chat_histories[user_id] = history[-30:]

            # OpenAI API call (sync, but can be made async with run_in_executor)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=user_chat_histories[user_id],
                    temperature=0.7,
                    max_tokens=MAX_TOKENS,
                )
            )

            reply = response.choices[0].message['content']
            user_chat_histories[user_id].append({"role": "assistant", "content": reply})
            return reply
        except openai.error.OpenAIError as e:
            logger.error(f"OpenAI API error: {e}", exc_info=True)
            return "Sorry, there was an issue with the OpenAI API."
        except Exception as e:
            logger.error(f"Error generating OpenAI response: {e}", exc_info=True)
            return "Sorry, something went wrong while generating a response."

    def is_rate_limited(self, user_id: int) -> bool:
        current_time = time.monotonic()
        last_time = user_last_interaction.get(user_id, 0)
        if current_time - last_time < RATE_LIMIT_SECONDS:
            return True
        user_last_interaction[user_id] = current_time
        return False

def setup(bot):
    bot.add_cog(Chat(bot))










