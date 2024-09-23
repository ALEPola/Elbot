import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import openai
import httpx
import os
from dotenv import load_dotenv
import time
import asyncio
import tempfile
import platform

# Load environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable not set.")

openai.api_key = OPENAI_API_KEY

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []

    async def generate_tts(self, text: str, output_file: str):
        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "tts-1",
            "voice": "nova",
            "input": text
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=data)
                response.raise_for_status()
                with open(output_file, "wb") as f:
                    f.write(response.content)
        except Exception as e:
            logger.error(f"TTS API Exception: {e}", exc_info=True)

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

        await interaction.followup.send(f"Joined {channel}!")

    @nextcord.slash_command(name="leave", description="Leave the voice channel.", guild_ids=[761070952674230292])
    async def leave(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_connected():
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message(f"{interaction.user.display_name}, I've left the voice channel.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, I'm not connected to any voice channel.", ephemeral=True)

    @nextcord.slash_command(name="play", description="Play a song from YouTube.", guild_ids=[761070952674230292])
    async def play(self, interaction: nextcord.Interaction, search: str):
        await interaction.response.defer()
        user = interaction.user
        await self.join(interaction)

        try:
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

            announcement_text = f"Now playing: {title}"
            tts_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            await self.generate_tts(announcement_text, tts_file.name)

            source = nextcord.FFmpegPCMAudio(tts_file.name)
            interaction.guild.voice_client.play(nextcord.PCMVolumeTransformer(source), after=lambda e: asyncio.run_coroutine_threadsafe(self.play_song(interaction, url, title), self.bot.loop))
        else:
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
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'noplaylist': True
        }
        for attempt in range(retries):
            try:
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if search_result and 'entries' in search_result and len(search_result['entries']) > 0:
                        info = search_result['entries'][0]
                        return info['url'], info['title']
            except youtube_dl.DownloadError as e:
                logger.error(f"Error extracting info from YouTube: {e}")
                if attempt < retries - 1:
                    logger.info(f"Retrying... ({attempt + 1}/{retries})")
                    time.sleep(2 ** attempt)
                else:
                    raise
        return None, None

    @nextcord.slash_command(name="volume", description="Set the bot's volume.", guild_ids=[761070952674230292])
    async def volume(self, interaction: nextcord.Interaction, volume: int):
        if interaction.guild.voice_client is None:
            return await interaction.response.send_message("I'm not connected to a voice channel.", ephemeral=True)
        
        interaction.guild.voice_client.source.volume = volume / 100
        await interaction.response.send_message(f"Volume set to {volume}%")

    @nextcord.slash_command(name="pause", description="Pause the currently playing song.", guild_ids=[761070952674230292])
    async def pause(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
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

    @nextcord.slash_command(name="stop", description="Stop the currently playing song.", guild_ids=[761070952674230292])
    async def stop(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(f"{interaction.user.display_name}, stopped the song.")
        else:
            await interaction.response.send_message(f"{interaction.user.display_name}, no song is currently playing.", ephemeral=True)

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