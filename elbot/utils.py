# elbot/utils.py

import os
from nextcord.ext import commands


def load_all_cogs(bot: commands.Bot, cogs_dir: str = "cogs"):
    """
    Dynamically load every .py file in the cogs/ directory as a cog.
    """
    for filename in os.listdir(cogs_dir):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        module_name = filename[:-3]
        extension = f"{cogs_dir.replace('/', '.')}.{module_name}"
        try:
            bot.load_extension(extension)
            print(f"✅ Loaded cog: {extension}")
        except commands.ExtensionAlreadyLoaded:
            print(f"⚠️ Cog already loaded: {extension}")
        except Exception as e:
            print(f"❌ Failed to load {extension}: {e}")
