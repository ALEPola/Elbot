import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
import time
from dotenv import load_dotenv
from nextcord import Embed
from nextcord.ui import View, Button

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

        @nextcord.slash_command(name="play", description="Play a song from YouTube.")
        async def play(self, interaction: nextcord.Interaction, search: str):
            await interaction.response.defer()
            if not await self.ensure_voice(interaction):
                return

            guild_id = interaction.guild.id
            result = await self.download_youtube_audio(search)
            
            if result is None:
                await interaction.followup.send("❌ Could not find the video.")
                return

            for item in result:
                await self.queue[guild_id].put((item["url"], item["title"]))

            if not interaction.guild.voice_client.is_playing():
                await self.play_next(guild_id)

            # Extract video details
            video_url = result[0]["url"]
            title = result[0]["title"][:1020] + "..." if len(result[0]["title"]) > 1024 else result[0]["title"]
            thumbnail_url = f"https://img.youtube.com/vi/{video_url.split('=')[-1]}/hqdefault.jpg"
            song_duration = "Unknown"
            artist = "Unknown"

            # Embed response with truncated fields
            embed = nextcord.Embed(title="🎵 Now Playing", color=0x3498db)
            embed.set_thumbnail(url=thumbnail_url)
            embed.add_field(name="🎶 Song", value=f"[{title}]({video_url})", inline=False)
            embed.add_field(name="⏳ Duration", value=f"**({song_duration})**", inline=True)
            embed.add_field(name="🎤 Artist", value=artist, inline=True)
            embed.set_footer(text=f"Requested by {interaction.user.display_name}", icon_url=interaction.user.avatar.url)

            await interaction.followup.send(embed=embed, view=self.create_music_controls(guild_id))


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

        if not os.path.exists(cookie_file):
            logger.error(f"❌ Cookie file not found: {cookie_file}")
            return None

        ydl_opts = {
            'format': 'bestaudio',
            'quiet': False,
            'noplaylist': False,
            'cookies': cookie_file,
            'geo_bypass': True,
            'nocheckcertificate': True
        }

        url_pattern = re.compile(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+')

        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                if url_pattern.match(query):
                    video_info = ydl.extract_info(query, download=False)
                    return [{"url": video_info["url"], "title": video_info.get("title", "Unknown Title")}] if "url" in video_info else None
                else:
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    return [{"url": search_result["entries"][0]["url"], "title": search_result["entries"][0]["title"]}] if search_result and "entries" in search_result else None
        except youtube_dl.DownloadError as e:
            logger.error(f"❌ YouTube-DL error: {e}")
            return None

    def create_music_controls(self, guild_id):
        return MusicControls(self.bot, guild_id)


class MusicControls(View):
    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    @nextcord.ui.button(label="QUEUE", style=nextcord.ButtonStyle.green)
    async def queue(self, button: Button, interaction: nextcord.Interaction):
        await interaction.response.send_message("/queue command placeholder", ephemeral=True)

    @nextcord.ui.button(label="PAUSE/RESUME", style=nextcord.ButtonStyle.gray)
    async def pause_resume(self, button: Button, interaction: nextcord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Paused the song.")
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Resumed the song.")

    @nextcord.ui.button(label="SKIP", style=nextcord.ButtonStyle.green)
    async def skip(self, button: Button, interaction: nextcord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped the song.")

    @nextcord.ui.button(label="STOP", style=nextcord.ButtonStyle.red)
    async def stop(self, button: Button, interaction: nextcord.Interaction):
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("⏹️ Stopped playback.")

def setup(bot):
    bot.add_cog(Music(bot))
