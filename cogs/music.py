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
            await self.queue.setdefault(guild_id, asyncio.Queue()).put((item["url"], item["title"]))

        if not interaction.guild.voice_client.is_playing():
            await self.play_next(guild_id)
        else:
            await interaction.followup.send(f"🎵 Added **{result[0]['title']}** to the queue.")

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
        """Plays a song and sends an embed with playback controls."""
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

        # ✅ FIX: Shorten title if too long
        title = title[:100] + "..." if len(title) > 100 else title

        # ✅ FIX: Ensure embed fields are under 1024 chars
        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=title[:1024], inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({url})", inline=False)
        embed.set_footer(text="Song By: Manu Chao | Esperanza")

        # Send message with controls
        view = self.create_music_controls()
        await voice_client.guild.system_channel.send(embed=embed, view=view)

    def create_music_controls(self):
        """Creates a view with music player buttons."""
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
        """Downloads the best audio format from YouTube, using cookies for authentication."""
        cookie_file = os.getenv('YOUTUBE_COOKIES_PATH', '/home/alex/Documents/youtube_cookies.txt')

        # ✅ FIX: Ensure the cookie file exists
        if not os.path.exists(cookie_file):
            logger.warning(f"⚠️ Cookie file not found: {cookie_file}. Continuing without it.")
            cookie_file = None  # Fallback to no cookies

        ydl_opts = {
            'format': 'bestaudio',
            'quiet': False,
            'noplaylist': False,
            'cookies': cookie_file if cookie_file else None,
            'geo_bypass': True,
            'nocheckcertificate': True
        }

        url_pattern = re.compile(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+')

        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                if url_pattern.match(query):
                    logger.info(f"🔎 Extracting direct URL: {query}")
                    video_info = ydl.extract_info(query, download=False)
                    return [{"url": video_info["url"], "title": video_info["title"]}]
                else:
                    logger.info(f"🔍 Searching YouTube for: {query}")
                    search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
                    if search_result and "entries" in search_result and len(search_result["entries"]) > 0:
                        video_info = search_result["entries"][0]
                        return [{"url": video_info["url"], "title": video_info["title"]}]
        except youtube_dl.DownloadError as e:
            logger.error(f"❌ YouTube-DL error: {e}")
            return None

def setup(bot):
    bot.add_cog(Music(bot))


