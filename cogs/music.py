import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # Dictionary of asyncio.Queue per guild
        self.last_activity_time = {}
        self.inactivity_timeout = 300  # 5 minutes

    async def start_inactivity_timer(self, guild_id):
        """Automatically disconnects the bot after a period of inactivity."""
        while guild_id in self.last_activity_time:
            if time.monotonic() - self.last_activity_time[guild_id] > self.inactivity_timeout:
                guild = self.bot.get_guild(guild_id)
                if guild and guild.voice_client and not guild.voice_client.is_playing() and self.queue[guild_id].empty():
                    await guild.voice_client.disconnect()
                    logger.info(f"Disconnected from {guild.name} due to inactivity.")
                    self.last_activity_time.pop(guild_id, None)
                return
            await asyncio.sleep(10)

    async def update_activity(self, guild_id):
        """Updates activity timestamp and starts inactivity timer if needed."""
        self.last_activity_time[guild_id] = time.monotonic()
        if guild_id not in self.queue:
            self.queue[guild_id] = asyncio.Queue()

        if len(self.last_activity_time) == 1:
            self.bot.loop.create_task(self.start_inactivity_timer(guild_id))

    async def ensure_voice(self, interaction):
        """Ensures the bot joins the user's voice channel."""
        if interaction.user.voice is None:
            await interaction.followup.send(f"{interaction.user.display_name}, you are not in a voice channel.", ephemeral=True)
            return False

        channel = interaction.user.voice.channel
        if interaction.guild.voice_client is None:
            await channel.connect()
        elif interaction.guild.voice_client.channel != channel:
            await interaction.guild.voice_client.move_to(channel)

        await self.update_activity(interaction.guild.id)
        return True

    @nextcord.slash_command(name="join", description="Join the voice channel.")
    async def join(self, interaction: nextcord.Interaction):
        if await self.ensure_voice(interaction):
            await interaction.followup.send(f"Joined {interaction.user.voice.channel}!")

    @nextcord.slash_command(name="leave", description="Leave the voice channel.")
    async def leave(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
            await interaction.guild.voice_client.disconnect()
            self.last_activity_time.pop(guild_id, None)
            self.queue.pop(guild_id, None)
            await interaction.response.send_message("Left the voice channel.")
        else:
            await interaction.response.send_message("I'm not connected to any voice channel.", ephemeral=True)

    @nextcord.slash_command(name="play", description="Play a song from YouTube.")
    async def play(self, interaction: nextcord.Interaction, search: str):
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        result = await self.download_youtube_audio(search)
        if result is None:
            await interaction.followup.send(f"Could not find the video.")
            return

        for item in result:
            await self.queue[guild_id].put((item["url"], item["title"]))

        if not interaction.guild.voice_client.is_playing():
            await self.play_next(guild_id)
        else:
            await interaction.followup.send(f"Added {result[0]['title']} to the queue.")

    async def play_next(self, guild_id):
        """Plays the next song in the queue."""
        if guild_id not in self.queue or self.queue[guild_id].empty():
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return

        url, title = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, url, title)

    async def play_song(self, voice_client, url, title):
        """Plays a song and sets up the next track."""
        ffmpeg_opts = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        source = nextcord.FFmpegPCMAudio(url, **ffmpeg_opts)
        voice_client.play(
            nextcord.PCMVolumeTransformer(source),
            after=lambda _: self.bot.loop.create_task(self.play_next(voice_client.guild.id))
        )
        logger.info(f"Now playing: {title}")

    async def download_youtube_audio(self, query):
        """Downloads the best audio format from YouTube, using cookies for authentication."""
        cookie_file = os.getenv('YOUTUBE_COOKIES_PATH', '/home/alex/Documents/youtube_cookies.txt')

        # Check if the cookie file exists before using it
        if not os.path.exists(cookie_file):
            logger.error(f"❌ Cookie file not found: {cookie_file}")
            return None

        ydl_opts = {
            'format': 'bestaudio',
            'quiet': False,  # Debugging enabled
            'noplaylist': False,
            'cookies': cookie_file,  # Use the cookie file
            'geo_bypass': True,  # Helps with region-locked videos
            'nocheckcertificate': True  # Bypass SSL issues
        }

        url_pattern = re.compile(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+')

        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                if url_pattern.match(query):
                    logger.info(f"🔎 Extracting direct URL: {query}")
                    video_info = ydl.extract_info(query, download=False)
                    
                    # Ensure video_info contains a URL
                    if "url" in video_info:
                        logger.info(f"✅ Successfully extracted video: {video_info.get('title', 'Unknown')}")
                        return [{"url": video_info["url"], "title": video_info.get("title", "Unknown Title")}]
                    else:
                        logger.error("❌ Extracted video info does not contain a URL.")
                        return None
                else:
                    logger.info(f"🔍 Searching YouTube for: {query}")
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)

                    if search_result and "entries" in search_result and len(search_result["entries"]) > 0:
                        video_info = search_result["entries"][0]
                        logger.info(f"✅ Found search result: {video_info.get('title', 'Unknown')}")
                        return [{"url": video_info["url"], "title": video_info["title"]}]
                    else:
                        logger.error("❌ No search results found.")
                        return None
        except youtube_dl.DownloadError as e:
            logger.error(f"❌ YouTube-DL error: {e}")
            return None


    @nextcord.slash_command(name="queue", description="Displays the current queue.")
    async def queue(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        queue_list = "\n".join([f"{index + 1}. {song[1]}" for index, song in enumerate(self.queue[guild_id]._queue)])
        await interaction.response.send_message(f"Current Queue:\n{queue_list}")

    @nextcord.slash_command(name="clear", description="Clear the entire queue.")
    async def clear(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        if guild_id in self.queue:
            self.queue[guild_id] = asyncio.Queue()
            await interaction.response.send_message("The queue has been cleared.")
        else:
            await interaction.response.send_message("The queue is already empty.", ephemeral=True)


def setup(bot):
    bot.add_cog(Music(bot))


