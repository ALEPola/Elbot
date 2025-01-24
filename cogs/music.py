import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
from dotenv import load_dotenv
import asyncio
import platform
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.last_activity_time = {}  # To track the last activity time for each guild
        self.inactivity_timeout = 300  # 5 minutes (in seconds)

    async def start_inactivity_timer(self, guild_id):
        while guild_id in self.last_activity_time:
            if asyncio.get_event_loop().time() - self.last_activity_time[guild_id] > self.inactivity_timeout:
                if guild := self.bot.get_guild(guild_id):
                    if guild.voice_client and guild.voice_client.is_connected():
                        await guild.voice_client.disconnect()
                        print(f"Disconnected from {guild.name} due to inactivity.")
                        self.last_activity_time.pop(guild_id, None)
                return  # Stop the timer for this guild
            await asyncio.sleep(10)

    async def update_activity(self, guild_id):
        self.last_activity_time[guild_id] = asyncio.get_event_loop().time()
        if len(self.last_activity_time) == 1:  # Start the timer only once
            self.bot.loop.create_task(self.start_inactivity_timer(guild_id))

    @nextcord.slash_command(name="join", description="Join the voice channel.", guild_ids=[761070952674230292])
    async def join(self, interaction: nextcord.Interaction):
        if interaction.user.voice is None:
            await interaction.followup.send(f"{interaction.user.display_name}, you are not in a voice channel.", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        if interaction.guild.voice_client is not None:
            await interaction.guild.voice_client.move_to(channel)
        else:
            await channel.connect()

        await self.update_activity(interaction.guild.id)  # Update activity timestamp
        await interaction.followup.send(f"Joined {channel}!")

    @nextcord.slash_command(name="leave", description="Leave the voice channel.", guild_ids=[761070952674230292])
    async def leave(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
            await interaction.guild.voice_client.disconnect()
            self.last_activity_time.pop(interaction.guild.id, None)  # Remove from activity tracker
            await interaction.response.send_message(f"{interaction.user.display_name}, I've left the voice channel.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, I'm not connected to any voice channel.", ephemeral=True)

    @nextcord.slash_command(name="play", description="Play a song from YouTube.", guild_ids=[761070952674230292])
    async def play(self, interaction: nextcord.Interaction, search: str):
        await interaction.response.defer()
        if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
            await self.update_activity(interaction.guild.id)

        user = interaction.user
        await self.join(interaction)

        try:
            # Detect if input is a URL or search query
            url, title = await self.download_youtube_audio(search)
            if url is None:
                await interaction.followup.send(f"{user.display_name}, an error occurred while downloading the video.")
                return

            self.queue.append((url, title))
            if not interaction.guild.voice_client.is_playing():
                await self.play_next(interaction)
            else:
                await interaction.followup.send(f"{user.display_name}, added {title} to the queue.")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            await interaction.followup.send(f"{user.display_name}, an unexpected error occurred: {e}")

    async def play_next(self, interaction: nextcord.Interaction):
        if self.queue:
            url, title = self.queue.pop(0)
            await self.play_song(interaction, url, title)
        else:
            if not interaction.response.is_done():
                await interaction.followup.send("The queue is empty.")

    async def play_song(self, interaction: nextcord.Interaction, url, title):
        while interaction.guild.voice_client.is_playing():
            await asyncio.sleep(1)

        ffmpeg_opts = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn',
            'executable': self.get_ffmpeg_executable()
        }
        source = nextcord.FFmpegPCMAudio(url, **ffmpeg_opts)
        interaction.guild.voice_client.play(nextcord.PCMVolumeTransformer(source), after=lambda e: self.bot.loop.create_task(self.play_next(interaction)))
        await interaction.followup.send(f'Now playing: {title}')

    def get_ffmpeg_executable(self):
        if platform.system() == "Windows":
            return "C:/ffmpeg/bin/ffmpeg.exe"
        else:
            return "ffmpeg"

    async def download_youtube_audio(self, query, retries=3):
        # Added cookies to `ydl_opts`
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'noplaylist': True,
            'cookies': '/home/alex/Documents/youtube_cookies.txt',  # Path to your cookies file
        }

        for attempt in range(retries):
            try:
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    # Check if the input is a URL
                    parsed = urlparse(query)
                    if parsed.scheme in ('http', 'https') and parsed.netloc:  # It's a URL
                        info = ydl.extract_info(query, download=False)
                        return info['url'], info['title']
                    
                    # Otherwise, treat it as a search query
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if search_result and 'entries' in search_result and len(search_result['entries']) > 0:
                        info = search_result['entries'][0]
                        return info['url'], info['title']
            except youtube_dl.DownloadError as e:
                logger.error(f"Error extracting info from YouTube: {e}")
                if attempt < retries - 1:
                    logger.info(f"Retrying... ({attempt + 1}/{retries})")
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
        return None, None

    @nextcord.slash_command(name="pause", description="Pause the currently playing song.", guild_ids=[761070952674230292])
    async def pause(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await self.update_activity(interaction.guild.id)  # Update activity timestamp
            await interaction.response.send_message(f"{interaction.user.display_name}, paused the song.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, no song is playing.", ephemeral=True)

    @nextcord.slash_command(name="resume", description="Resume the currently paused song.", guild_ids=[761070952674230292])
    async def resume(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.response.send_message(f"{interaction.user.display_name}, resumed the song.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, the song is not paused.", ephemeral=True)

    @nextcord.slash_command(name="skip", description="Skip the currently playing song.", guild_ids=[761070952674230292])
    async def skip(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(f"{interaction.user.display_name}, skipped the song.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, no song is currently playing.", ephemeral=True)

    @nextcord.slash_command(name="queue", description="Displays the current queue.", guild_ids=[761070952674230292])
    async def queue(self, interaction: nextcord.Interaction):
        if not self.queue:
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        queue_list = "\n".join([f"{index + 1}. {song[1]}" for index, song in enumerate(self.queue)])
        await interaction.response.send_message(f"Current Queue:\n{queue_list}")

def setup(bot):
    bot.add_cog(Music(bot))



#new