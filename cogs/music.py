import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
from dotenv import load_dotenv
import asyncio
import platform
from urllib.parse import urlparse
import time

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.last_activity_time = {}
        self.inactivity_timeout = 300  # 5 minutes

    async def start_inactivity_timer(self, guild_id):
        while guild_id in self.last_activity_time:
            if time.monotonic() - self.last_activity_time[guild_id] > self.inactivity_timeout:
                if guild := self.bot.get_guild(guild_id):
                    if guild.voice_client and guild.voice_client.is_connected():
                        await guild.voice_client.disconnect()
                        logger.info(f"Disconnected from {guild.name} due to inactivity.")
                        self.last_activity_time.pop(guild_id, None)
                return
            await asyncio.sleep(10)

    async def update_activity(self, guild_id):
        self.last_activity_time[guild_id] = time.monotonic()
        if len(self.last_activity_time) == 1:
            self.bot.loop.create_task(self.start_inactivity_timer(guild_id))

    async def ensure_voice(self, interaction):
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

    @nextcord.slash_command(name="join", description="Join the voice channel.", guild_ids=[761070952674230292])
    async def join(self, interaction: nextcord.Interaction):
        if await self.ensure_voice(interaction):
            await interaction.followup.send(f"Joined {interaction.user.voice.channel}!")

    @nextcord.slash_command(name="leave", description="Leave the voice channel.", guild_ids=[761070952674230292])
    async def leave(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
            await interaction.guild.voice_client.disconnect()
            self.last_activity_time.pop(interaction.guild.id, None)
            await interaction.response.send_message(f"{interaction.user.display_name}, I've left the voice channel.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, I'm not connected to any voice channel.", ephemeral=True)

    @nextcord.slash_command(name="play", description="Play a song from YouTube.", guild_ids=[761070952674230292])
    async def play(self, interaction: nextcord.Interaction, search: str):
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        try:
            url, title = await self.download_youtube_audio(search)
            if url is None:
                await interaction.followup.send(f"Could not download the video.")
                return

            self.queue.append((url, title))
            if not interaction.guild.voice_client.is_playing():
                await self.play_next(interaction)
            else:
                await interaction.followup.send(f"Added {title} to the queue.")
        except Exception as e:
            logger.error(f"Error in play command: {e}")
            await interaction.followup.send("An unexpected error occurred.")

    async def play_next(self, interaction: nextcord.Interaction):
        if self.queue:
            url, title = self.queue.pop(0)
            await self.play_song(interaction, url, title)
        else:
            await interaction.followup.send("The queue is empty.", ephemeral=True)

    async def play_song(self, interaction: nextcord.Interaction, url, title):
        while interaction.guild.voice_client.is_playing():
            await asyncio.sleep(1)

        ffmpeg_opts = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        source = nextcord.FFmpegPCMAudio(url, **ffmpeg_opts)
        interaction.guild.voice_client.play(
            nextcord.PCMVolumeTransformer(source),
            after=lambda e: self.bot.loop.create_task(self.play_next(interaction))
        )
        await interaction.followup.send(f'Now playing: {title}')

    async def download_youtube_audio(self, query, retries=3):
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'noplaylist': False,  # Allow playlists to be processed
            'cookies': os.getenv('YOUTUBE_COOKIES_PATH', '/home/alex/Documents/youtube_cookies.txt'),
        }

        for attempt in range(retries):
            try:
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    parsed = urlparse(query)

                    # Check if it's a playlist
                    if parsed.scheme in ('http', 'https') and parsed.netloc and "playlist" in query:
                        playlist_info = ydl.extract_info(query, download=False)
                        if "entries" in playlist_info:
                            return [
                                {"url": entry["url"], "title": entry["title"]}
                                for entry in playlist_info["entries"]
                            ]
                    
                    # Handle single video or search query
                    if parsed.scheme in ('http', 'https') and parsed.netloc:
                        video_info = ydl.extract_info(query, download=False)
                        return [{"url": video_info["url"], "title": video_info["title"]}]

                    # Handle search query
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if search_result and "entries" in search_result and len(search_result["entries"]) > 0:
                        video_info = search_result["entries"][0]
                        return [{"url": video_info["url"], "title": video_info["title"]}]
            except youtube_dl.DownloadError as e:
                logger.error(f"Error extracting info from YouTube: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        return None
   

    @nextcord.slash_command(name="pause", description="Pause the currently playing song.", guild_ids=[761070952674230292])
    async def pause(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.response.send_message("Paused the song.")
        else:
            await interaction.response.send_message("No song is currently playing.", ephemeral=True)

    @nextcord.slash_command(name="resume", description="Resume the currently paused song.", guild_ids=[761070952674230292])
    async def resume(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.response.send_message("Resumed the song.")
        else:
            await interaction.response.send_message("The song is not paused.", ephemeral=True)

    @nextcord.slash_command(name="skip", description="Skip the currently playing song.", guild_ids=[761070952674230292])
    async def skip(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("Skipped the song.")
        else:
            await interaction.response.send_message("No song is currently playing.", ephemeral=True)

    @nextcord.slash_command(name="queue", description="Displays the current queue.", guild_ids=[761070952674230292])
    async def queue(self, interaction: nextcord.Interaction):
        if not self.queue:
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        queue_list = "\n".join([f"{index + 1}. {song[1]}" for index, song in enumerate(self.queue)])
        await interaction.response.send_message(f"Current Queue:\n{queue_list}")

    @nextcord.slash_command(name="volume", description="Adjust playback volume (0-100%).", guild_ids=[761070952674230292])
    async def volume(self, interaction: nextcord.Interaction, level: int):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            if 0 <= level <= 100:
                source = interaction.guild.voice_client.source
                if isinstance(source, nextcord.PCMVolumeTransformer):
                    source.volume = level / 100
                else:
                    interaction.guild.voice_client.source = nextcord.PCMVolumeTransformer(source, volume=level / 100)
                await interaction.response.send_message(f"Volume set to {level}%.")
            else:
                await interaction.response.send_message("Provide a volume level between 0 and 100.", ephemeral=True)
        else:
            await interaction.response.send_message("No audio is currently playing.", ephemeral=True)

    @nextcord.slash_command(name="remove", description="Remove a song from the queue by its position.", guild_ids=[761070952674230292])
    async def remove(self, interaction: nextcord.Interaction, position: int):
        if 1 <= position <= len(self.queue):
            removed_song = self.queue.pop(position - 1)
            await interaction.response.send_message(f"Removed: {removed_song[1]} (Position {position}).")
        else:
            await interaction.response.send_message("Invalid position. Check the queue and try again.", ephemeral=True)

    @nextcord.slash_command(name="clear", description="Clear the entire queue.", guild_ids=[761070952674230292])
    async def clear(self, interaction: nextcord.Interaction):
        if self.queue:
            self.queue.clear()
            await interaction.response.send_message("The queue has been cleared.")
        else:
            await interaction.response.send_message("The queue is already empty.", ephemeral=True)


def setup(bot):
    bot.add_cog(Music(bot))
