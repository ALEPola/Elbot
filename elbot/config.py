# elbot/config.py

import os
from pathlib import Path
from dotenv import load_dotenv

# Look for a “.env” file one level up (in your project root)
BASE_DIR = Path(__file__).parent.parent
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)


class Config:
    # Your Discord bot token must live in an environment variable called DISCORD_BOT_TOKEN
    DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

    # The command prefix (e.g. “!”, or “/” if you prefer slash commands only)
    PREFIX = os.getenv("COMMAND_PREFIX", "!")

    # (Optional) If you want to store a guild ID for guild-specific logic
    GUILD_ID = int(os.getenv("GUILD_ID", "0")) if os.getenv("GUILD_ID") else None

    @classmethod
    def validate(cls):
        missing = []
        if not cls.DISCORD_TOKEN:
            missing.append("DISCORD_BOT_TOKEN")
        if not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
