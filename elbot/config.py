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
    """Central configuration loaded from environment variables.

    `ICS_URL` defaults to an empty string and `F1_CHANNEL_ID` defaults to
    ``0`` when the corresponding variables are not set.
    """

    # Your Discord bot token must live in an environment variable called DISCORD_BOT_TOKEN
    BASE_DIR = BASE_DIR
    DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
    ICS_URL = os.getenv("ICS_URL", "")
    _f1_channel_str = os.getenv("F1_CHANNEL_ID")
    if _f1_channel_str:
        try:
            F1_CHANNEL_ID = int(_f1_channel_str)
        except ValueError:
            logger.warning(
                "Invalid F1_CHANNEL_ID '%s' - expected integer", _f1_channel_str
            )
            F1_CHANNEL_ID = 0
    else:
        F1_CHANNEL_ID = 0

    # The command prefix (e.g. “!”, or “/” if you prefer slash commands only)
    PREFIX = os.getenv("COMMAND_PREFIX", "!")

    # Lavalink connection details
    LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
    LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
    LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

    # (Optional) If you want to store a guild ID for guild-specific logic
    if _gid_str:
        try:
            GUILD_ID = int(_gid_str)
        except ValueError:
            logger.warning("Invalid GUILD_ID '%s' - expected integer", _gid_str)
            GUILD_ID = None
    else:
        GUILD_ID = None

    @staticmethod
    def validate():
        import os

        auto = os.getenv("AUTO_LAVALINK", "1") == "1"

        required = ["DISCORD_BOT_TOKEN"]
        if not auto:
            required += ["LAVALINK_HOST", "LAVALINK_PASSWORD"]

        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
