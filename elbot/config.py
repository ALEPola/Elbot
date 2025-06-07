# elbot/config.py

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Look for a “.env” file one level up (in your project root)
BASE_DIR = Path(__file__).parent.parent
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)

logger = logging.getLogger("elbot.config")
_gid_str = os.getenv("GUILD_ID")


class Config:
    # Your Discord bot token must live in an environment variable called DISCORD_BOT_TOKEN
    DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")

    # The command prefix (e.g. “!”, or “/” if you prefer slash commands only)
    PREFIX = os.getenv("COMMAND_PREFIX", "!")

    # (Optional) If you want to store a guild ID for guild-specific logic
    if _gid_str:
        try:
            GUILD_ID = int(_gid_str)
        except ValueError:
            logger.warning("Invalid GUILD_ID '%s' - expected integer", _gid_str)
            GUILD_ID = None
    else:
        GUILD_ID = None

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
