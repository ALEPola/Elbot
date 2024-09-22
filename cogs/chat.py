import nextcord
from nextcord.ext import commands
import openai
import os
import logging
import asyncio
from textblob import TextBlob
from dotenv import load_dotenv  # Import to load .env file

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

# Set a rate limit (in seconds)
RATE_LIMIT_SECONDS = 5

class Chat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def preprocess_text(self, text: str) -> str:
        """Preprocess input text."""
        return text.lower().strip()

    @nextcord.slash_command(name="chat", description="Chat with the bot using OpenAI.")
    async def chat(self, interaction: nextcord.Interaction, message: str):
        """Slash command to chat with OpenAI."""
        user_id = interaction.user.id

        # Check rate limit
        if self.is_rate_limited(user_id):
            await interaction.response.send_message("You're asking too frequently, please wait a moment.", ephemeral=True)
            return

        # **Defer the response to avoid timeouts**
        await interaction.response.defer()

        # Preprocess the user message
        message_preprocessed = self.preprocess_text(message)

        # Sentiment analysis with TextBlob
        sentiment = TextBlob(message_preprocessed).sentiment
        logger.info(f"User {user_id} sentiment analysis: {sentiment}")

        # Handle negative sentiment
        if sentiment.polarity < -0.5:
            await interaction.followup.send("It seems like you're upset. How can I help?")
            return

        # Call OpenAI API to generate response
        response_text = await self.generate_openai_response(user_id, message_preprocessed)

        # Send the generated response as a follow-up message
        await interaction.followup.send(response_text)

    async def generate_openai_response(self, user_id: int, message: str) -> str:
        try:
            # Maintain user chat history for context
            if user_id not in user_chat_histories:
                user_chat_histories[user_id] = []

            # Append the user's message to history
            user_chat_histories[user_id].append({"role": "user", "content": message})

            # Limit the history to 10 messages
            user_chat_histories[user_id] = user_chat_histories[user_id][-30:]

            # Prepare API request
            response = client.chat.completions.create(
                model="gpt-4o",  # Adjust to the model you're using
                messages=user_chat_histories[user_id],
                temperature=0.7,  # Adjust temperature for randomness
                max_tokens=200,  # Limit the response length
            )

            # Extract and append the assistant's response to the chat history
            reply = response.choices[0].message.content  # Fixed this line
            user_chat_histories[user_id].append({"role": "assistant", "content": reply})

            return reply
        except Exception as e:
            logger.error(f"Error generating OpenAI response: {e}", exc_info=True)
            return "Sorry, something went wrong while generating a response."


    def is_rate_limited(self, user_id: int) -> bool:
        """Check if the user is being rate-limited."""
        current_time = asyncio.get_event_loop().time()
        last_interaction_time = user_last_interaction.get(user_id, 0)

        if current_time - last_interaction_time < RATE_LIMIT_SECONDS:
            return True
        user_last_interaction[user_id] = current_time
        return False

# Setup the cog
def setup(bot):
    bot.add_cog(Chat(bot))










