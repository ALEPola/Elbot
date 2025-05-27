import nextcord
from nextcord.ext import commands
import json
import os

# Basic localization dictionary
localizations = {
    "en": {
        "play": "Play a song from YouTube.",
        "queue_details": "View detailed queue information.",
        "remove_track": "Remove a track from the queue by its position.",
        "move_track": "Move a track to a new position in the queue.",
        "search": "Search for a track and select one to queue.",
        "volume": "Adjust playback volume (0-150%).",
        "help": "Show help message."
    },
    "es": {
        "play": "Reproducir una canción de YouTube.",
        "queue_details": "Ver información detallada de la cola.",
        "remove_track": "Eliminar una pista de la cola por su posición.",
        "move_track": "Mover una pista a una nueva posición en la cola.",
        "search": "Buscar una pista y seleccionarla para agregar a la cola.",
        "volume": "Ajustar el volumen de reproducción (0-150%).",
        "help": "Mostrar el mensaje de ayuda."
    }
}

user_languages = {}

class Localization(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.default_language = "en"
        self.translations = {}
        self.load_translations()

    def load_translations(self):
        """Load translation files from the 'locales' directory."""
        locales_dir = os.path.join(os.path.dirname(__file__), "..", "locales")
        for filename in os.listdir(locales_dir):
            if filename.endswith(".json"):
                lang_code = filename.split(".")[0]
                with open(os.path.join(locales_dir, filename), "r", encoding="utf-8") as f:
                    self.translations[lang_code] = json.load(f)

    def translate(self, key, language=None):
        """Translate a key into the specified language."""
        language = language or self.default_language
        return self.translations.get(language, {}).get(key, key)

    @nextcord.slash_command(name="set_language", description="Set your preferred language (e.g., en, es).")
    async def set_language(self, interaction: nextcord.Interaction, language: str):
        if language not in localizations:
            await interaction.response.send_message("Language not supported. Supported languages: " + ", ".join(localizations.keys()), ephemeral=True)
            return
        user_languages[interaction.user.id] = language
        await interaction.response.send_message(f"Language set to {language}.", ephemeral=True)

# Ensure the Localization cog is initialized with the required 'bot' argument in the main bot file.
# Example:
# bot.add_cog(Localization(bot))

def setup(bot):
    bot.add_cog(Localization(bot))
