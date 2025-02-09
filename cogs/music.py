import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # Maps guild_id to an asyncio.Queue of songs.
        self.player_messages = {}  # Maps guild_id to the persistent player message.
        self.player_channels = {}  # Maps guild_id to the channel where the player message is sent.

    async def ensure_voice(self, interaction: nextcord.Interaction):
        """Ensure the bot joins the user's voice channel."""
        if interaction.user.voice is None:
            await interaction.followup.send(
                f"{interaction.user.display_name}, you are not in a voice channel.", 
                ephemeral=True
            )
            return False

        channel = interaction.user.voice.channel
        if interaction.guild.voice_client is None:
            await channel.connect()
        elif interaction.guild.voice_client.channel != channel:
            await interaction.guild.voice_client.move_to(channel)
        return True

    @nextcord.slash_command(name="play", description="Play a song from YouTube.")
    async def play(self, interaction: nextcord.Interaction, search: str):
        # Defer the response so the bot doesn't show "thinking" indefinitely.
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        # Save the channel to use for sending/updating the player message.
        self.player_channels[guild_id] = interaction.channel

        result = await self.download_youtube_audio(search)
        if result is None:
            await interaction.followup.send("❌ Could not find the video.")
            return

        # Enqueue each item (typically one video).
        for item in result:
            await self.queue.setdefault(guild_id, asyncio.Queue()).put(item)

        # If nothing is playing, start playback.
        if not interaction.guild.voice_client.is_playing():
            await self.play_next(guild_id, interaction)
        else:
            await interaction.followup.send(f"🎵 Added **{result[0]['title']}** to the queue.")

    async def play_next(self, guild_id, interaction: nextcord.Interaction = None):
        """Retrieve the next song in the queue and play it."""
        if guild_id not in self.queue or self.queue[guild_id].empty():
            # Optionally, you can update the persistent message to say the queue is empty.
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            if interaction:
                await interaction.followup.send("Bot is not connected to a voice channel.")
            return

        item = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, item, interaction)

    async def play_song(self, voice_client, item, interaction: nextcord.Interaction = None):
        """Play the song, update (or create) the persistent player message, and add controls."""
        url = item.get("url")
        title = item.get("title")
        thumbnail = item.get("thumbnail")

        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        source = nextcord.FFmpegPCMAudio(url, **ffmpeg_opts)
        voice_client.play(
            nextcord.PCMVolumeTransformer(source),
            after=lambda e: self.bot.loop.create_task(self.play_next(voice_client.guild.id))
        )
        logger.info(f"Now playing: {title}")

        # Shorten the title for display if it is too long.
        title_display = title[:100] + "..." if len(title) > 100 else title

        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=title_display[:1024], inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({url})", inline=False)
        embed.set_footer(text="Song By: Manu Chao | Esperanza")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        view = self.create_music_controls()

        # Update the persistent player message if it exists; otherwise, send a new one.
        guild_id = voice_client.guild.id
        if guild_id in self.player_messages:
            try:
                await self.player_messages[guild_id].edit(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Error editing player message: {e}")
        else:
            # Use the stored channel from the command; fallback to system_channel or the first text channel.
            channel = self.player_channels.get(guild_id)
            if not channel:
                if voice_client.guild.system_channel:
                    channel = voice_client.guild.system_channel
                else:
                    channel = voice_client.guild.text_channels[0]
            try:
                msg = await channel.send(embed=embed, view=view)
                self.player_messages[guild_id] = msg
            except Exception as e:
                logger.error(f"Error sending player message: {e}")

        # If the original interaction is still available, update it.
        if interaction:
            try:
                await interaction.edit_original_message(content="Now playing:")
            except Exception as e:
                logger.error(f"Error editing original interaction message: {e}")

    def create_music_controls(self):
        """Builds a view with music player buttons."""
        view = nextcord.ui.View()
        view.add_item(nextcord.ui.Button(label="QUEUE", custom_id="queue", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="BACK", custom_id="back", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="⏯ Pause/Resume", custom_id="pause_resume", style=nextcord.ButtonStyle.grey))
        view.add_item(nextcord.ui.Button(label="SKIP", custom_id="skip", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="AUTOPLAY", custom_id="autoplay", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="LOOP", custom_id="loop", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="REWIND", custom_id="rewind", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="🛑 STOP", custom_id="stop", style=nextcord.ButtonStyle.red))
        view.add_item(nextcord.ui.Button(label="FORWARD", custom_id="forward", style=nextcord.ButtonStyle.green))
        view.add_item(nextcord.ui.Button(label="REPLAY", custom_id="replay", style=nextcord.ButtonStyle.green))
        return view

    async def download_youtube_audio(self, query):
        """
        Retrieves video information from YouTube (using cookies if available) and returns a list
        of dictionaries containing the URL, title, and thumbnail.
        """
        cookie_file = os.getenv("YOUTUBE_COOKIES_PATH", "/home/alex/Documents/youtube_cookies.txt")
        if cookie_file and not os.path.exists(cookie_file):
            logger.warning(f"⚠️ Cookie file not found: {cookie_file}. Continuing without it.")
            cookie_file = None

        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "noplaylist": False,
            "cookies": cookie_file if cookie_file else None,
            "geo_bypass": True,
            "nocheckcertificate": True
        }

        url_pattern = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+")
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                if url_pattern.match(query):
                    logger.info(f"Extracting direct URL: {query}")
                    video_info = ydl.extract_info(query, download=False)
                    return [{
                        "url": video_info.get("url"),
                        "title": video_info.get("title"),
                        "thumbnail": video_info.get("thumbnail")
                    }]
                else:
                    logger.info(f"Searching YouTube for: {query}")
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if (
                        search_result 
                        and "entries" in search_result 
                        and len(search_result["entries"]) > 0
                    ):
                        video_info = search_result["entries"][0]
                        return [{
                            "url": video_info.get("url"),
                            "title": video_info.get("title"),
                            "thumbnail": video_info.get("thumbnail")
                        }]
        except youtube_dl.DownloadError as e:
            logger.error(f"YouTube-DL error: {e}")
            return None

def setup(bot):
    bot.add_cog(Music(bot))



