import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
from dotenv import load_dotenv
import functools

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Helper function to extract YouTube info; this is blocking.
def extract_info(query, ydl_opts, cookie_file):
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        url_pattern = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/.+")
        if url_pattern.match(query):
            logger.info(f"Extracting direct URL: {query}")
            video_info = ydl.extract_info(query, download=False)
            return [{
                "stream_url": video_info.get("url"),
                "page_url": video_info.get("webpage_url"),
                "title": video_info.get("title"),
                "thumbnail": video_info.get("thumbnail"),
                "artist": video_info.get("artist") or video_info.get("uploader") or "Unknown Artist",
                "uploader": video_info.get("uploader")
            }]
        else:
            logger.info(f"Searching YouTube for: {query}")
            search_result = ydl.extract_info(f"ytsearch:{query}", download=False)
            if search_result and "entries" in search_result and len(search_result["entries"]) > 0:
                video_info = search_result["entries"][0]
                return [{
                    "stream_url": video_info.get("url"),
                    "page_url": video_info.get("webpage_url"),
                    "title": video_info.get("title"),
                    "thumbnail": video_info.get("thumbnail"),
                    "artist": video_info.get("artist") or video_info.get("uploader") or "Unknown Artist",
                    "uploader": video_info.get("uploader")
                }]
    return None

# Updated persistent view with buttons for controlling playback.
class MusicControls(nextcord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)  # Persistent view (no timeout)
        self.cog = cog

    @nextcord.ui.button(label="QUEUE", style=nextcord.ButtonStyle.green, custom_id="queue")
    async def queue_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.show_queue(interaction)

    @nextcord.ui.button(label="⏯ Pause/Resume", style=nextcord.ButtonStyle.grey, custom_id="pause_resume")
    async def pause_resume_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.toggle_pause_resume(interaction)

    @nextcord.ui.button(label="SKIP", style=nextcord.ButtonStyle.green, custom_id="skip")
    async def skip_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.skip_track(interaction)

    @nextcord.ui.button(label="REWIND", style=nextcord.ButtonStyle.green, custom_id="rewind")
    async def rewind_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.rewind_track(interaction)

    @nextcord.ui.button(label="FORWARD", style=nextcord.ButtonStyle.green, custom_id="forward")
    async def forward_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.forward_track(interaction)

    @nextcord.ui.button(label="REPLAY", style=nextcord.ButtonStyle.green, custom_id="replay")
    async def replay_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.replay_track(interaction)

    @nextcord.ui.button(label="LOOP", style=nextcord.ButtonStyle.green, custom_id="loop")
    async def loop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.toggle_loop(interaction)

    @nextcord.ui.button(label="🛑 STOP", style=nextcord.ButtonStyle.red, custom_id="stop")
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.stop_track(interaction)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}            # guild_id -> asyncio.Queue of song items
        self.player_messages = {}  # guild_id -> persistent player message
        self.player_channels = {}  # guild_id -> channel to send the persistent message
        self.loop_mode = {}        # guild_id -> bool (True if looping current track)
        self.autoplay_mode = {}    # guild_id -> bool (toggle autoplay)
        self.current_track = {}    # guild_id -> current track (dict)
        self.history = {}          # guild_id -> list of previously played tracks

    async def ensure_voice(self, interaction: nextcord.Interaction):
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
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        self.player_channels[guild_id] = interaction.channel

        # Offload the blocking extraction to another thread.
        result = await self.download_youtube_audio(search)
        if result is None:
            await interaction.followup.send("❌ Could not find the video.")
            return

        for item in result:
            await self.queue.setdefault(guild_id, asyncio.Queue()).put(item)

        if not interaction.guild.voice_client.is_playing():
            await self.play_next(guild_id, interaction)
        else:
            await interaction.followup.send(f"🎵 Added **{result[0]['title']}** to the queue.")

    async def play_next(self, guild_id, interaction: nextcord.Interaction = None):
        if guild_id not in self.queue or self.queue[guild_id].empty():
            logger.info("Queue is empty, nothing to play next.")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            if interaction:
                await interaction.followup.send("Bot is not connected to a voice channel.")
            return

        item = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, item, interaction)

    async def play_song(self, voice_client, item, interaction: nextcord.Interaction = None):
        guild_id = voice_client.guild.id
        self.current_track[guild_id] = item

        stream_url = item.get("stream_url")
        page_url = item.get("page_url")
        title = item.get("title")
        thumbnail = item.get("thumbnail")
        artist = item.get("artist") or item.get("uploader") or "Unknown Artist"

        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        source = nextcord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)

        def after_callback(error):
            if self.loop_mode.get(guild_id, False):
                self.bot.loop.create_task(self.play_song(voice_client, item))
            else:
                self.bot.loop.create_task(self.play_next(guild_id))
        voice_client.play(nextcord.PCMVolumeTransformer(source), after=after_callback)
        logger.info(f"Now playing: {title}")

        title_display = title[:100] + "..." if len(title) > 100 else title
        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=title_display[:1024], inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({page_url})", inline=False)
        embed.set_footer(text=f"Song By: {artist}")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        view = MusicControls(self)
        if guild_id in self.player_messages:
            try:
                await self.player_messages[guild_id].edit(embed=embed, view=view)
                logger.info("Updated persistent player message.")
            except Exception as e:
                logger.error(f"Error editing player message: {e}")
        else:
            channel = self.player_channels.get(guild_id)
            if not channel:
                if voice_client.guild.system_channel:
                    channel = voice_client.guild.system_channel
                else:
                    channel = voice_client.guild.text_channels[0]
            try:
                msg = await channel.send(embed=embed, view=view)
                self.player_messages[guild_id] = msg
                logger.info("Sent new persistent player message.")
            except Exception as e:
                logger.error(f"Error sending player message: {e}")

        # The original interaction message is no longer edited to prevent an extra "Now playing:" message.

    async def download_youtube_audio(self, query):
        cookie_file = os.getenv("YOUTUBE_COOKIES_PATH", "/home/alex/Documents/youtube_cookies.txt")
        if cookie_file and not os.path.exists(cookie_file):
            logger.warning(f"Cookie file not found: {cookie_file}. Continuing without cookies.")
            cookie_file = None

        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "noplaylist": False,
            "cookies": cookie_file if cookie_file else None,
            "geo_bypass": True,
            "nocheckcertificate": True
        }
        # Run the blocking extraction in a separate thread.
        result = await asyncio.to_thread(extract_info, query, ydl_opts, cookie_file)
        return result

    # ----- Button Functionality -----

    async def toggle_pause_resume(self, interaction: nextcord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("Resumed playback.", ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("Paused playback.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    async def skip_track(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        if guild_id in self.current_track:
            self.history.setdefault(guild_id, []).append(self.current_track[guild_id])
        voice_client.stop()
        await interaction.response.send_message("Skipped track.", ephemeral=True)

    async def stop_track(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        voice_client.stop()
        if guild_id in self.queue:
            while not self.queue[guild_id].empty():
                try:
                    self.queue[guild_id].get_nowait()
                except asyncio.QueueEmpty:
                    break
        await interaction.response.send_message("Stopped playback and cleared the queue.", ephemeral=True)

    async def show_queue(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return
        queue_list = list(self.queue[guild_id]._queue)
        description = ""
        for i, item in enumerate(queue_list, start=1):
            description += f"{i}. {item.get('title')}\n"
        embed = nextcord.Embed(title="Queue", description=description, color=nextcord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def toggle_loop(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        current = self.loop_mode.get(guild_id, False)
        self.loop_mode[guild_id] = not current
        status = "enabled" if self.loop_mode[guild_id] else "disabled"
        await interaction.response.send_message(f"Loop mode {status}.", ephemeral=True)

    async def toggle_autoplay(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        current = self.autoplay_mode.get(guild_id, False)
        self.autoplay_mode[guild_id] = not current
        status = "enabled" if self.autoplay_mode[guild_id] else "disabled"
        await interaction.response.send_message(f"Autoplay {status}.", ephemeral=True)

    async def rewind_track(self, interaction: nextcord.Interaction):
        # Defer the response so Discord knows you're processing the interaction.
        await interaction.response.defer()
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if guild_id not in self.current_track:
            await interaction.followup.send("No track is currently playing.", ephemeral=True)
            return
        item = self.current_track[guild_id]
        voice_client.stop()
        await self.play_song(voice_client, item, interaction)
        await interaction.followup.send("Rewound the track (restarted).", ephemeral=True)

    async def forward_track(self, interaction: nextcord.Interaction):
        await self.skip_track(interaction)

    async def replay_track(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if guild_id not in self.current_track:
            await interaction.followup.send("No track is currently playing.", ephemeral=True)
            return
        item = self.current_track[guild_id]
        voice_client.stop()
        await self.play_song(voice_client, item, interaction)
        await interaction.followup.send("Replaying the track.", ephemeral=True)

def setup(bot):
    bot.add_cog(Music(bot))










