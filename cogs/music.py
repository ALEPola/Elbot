"""
MusicCog: A cog for managing music playback and related commands.

This cog provides commands to play, pause, skip, and manage music queues in a Discord server.
"""

import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
import time
from dotenv import load_dotenv
import functools
from nextcord.ext.commands import cooldown, BucketType

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

def extract_info(query, ydl_opts, cookie_file):
    """
    Extract video information from a query using youtube_dl.

    Args:
        query (str): The search query or URL.
        ydl_opts (dict): Options for youtube_dl.
        cookie_file (str): Path to the cookies file.

    Returns:
        tuple: A tuple containing the video information and an error message (if any).
    """
    # Quick check for additional services
    if "spotify" in query.lower() or "soundcloud" in query.lower():
        logger.info("Spotify/SoundCloud integration not yet implemented. Please use a YouTube link.")
        return None, "Unsupported service. Please use a YouTube link."

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
                "uploader": video_info.get("uploader"),
                "duration": video_info.get("duration")
            }], None
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
                    "uploader": video_info.get("uploader"),
                    "duration": video_info.get("duration")
                }], None
    return None, "Could not find the video."

# Helper function to clear and refill a queue
def update_queue(queue, new_items):
    """
    Update the queue with new items.

    Args:
        queue (asyncio.Queue): The queue to update.
        new_items (list): The new items to add to the queue.
    """
    queue._queue.clear()
    for item in new_items:
        queue._queue.append(item)

# Persistent view for music controls.
class MusicControls(nextcord.ui.View):
    """
    Persistent view for music controls.

    Attributes:
        cog (Music): The Music cog instance.
    """

    def __init__(self, cog):
        """
        Initialize the MusicControls view.

        Args:
            cog (Music): The Music cog instance.
        """
        super().__init__(timeout=None)
        self.cog = cog

    @nextcord.ui.button(label="QUEUE", style=nextcord.ButtonStyle.green, custom_id="queue")
    async def queue_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Show the queue details.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.queue_details(interaction)

    @nextcord.ui.button(label="⏯ Pause/Resume", style=nextcord.ButtonStyle.grey, custom_id="pause_resume")
    async def pause_resume_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Toggle pause/resume for the current track.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.toggle_pause_resume(interaction)

    @nextcord.ui.button(label="SKIP", style=nextcord.ButtonStyle.green, custom_id="skip")
    async def skip_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Skip the current track.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.skip_track(interaction)

    @nextcord.ui.button(label="REWIND", style=nextcord.ButtonStyle.green, custom_id="rewind")
    async def rewind_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Rewind the current track (restart).

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.rewind_track(interaction)

    @nextcord.ui.button(label="FORWARD", style=nextcord.ButtonStyle.green, custom_id="forward")
    async def forward_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Forward the current track (skip ahead).

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.forward_track(interaction)

    @nextcord.ui.button(label="REPLAY", style=nextcord.ButtonStyle.green, custom_id="replay")
    async def replay_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Replay the current track.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.replay_track(interaction)

    @nextcord.ui.button(label="LOOP", style=nextcord.ButtonStyle.green, custom_id="loop")
    async def loop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Toggle loop mode for the current track.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.toggle_loop(interaction)

    @nextcord.ui.button(label="🛑 STOP", style=nextcord.ButtonStyle.red, custom_id="stop")
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        """
        Stop the current track and clear the queue.

        Args:
            button (nextcord.ui.Button): The button that was clicked.
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.cog.stop_track(interaction)

class Music(commands.Cog):
    """
    A cog for managing music playback and related commands.

    Attributes:
        bot (commands.Bot): The bot instance.
        queue (dict): A dictionary mapping guild IDs to their music queues.
        locks (dict): A dictionary mapping guild IDs to asyncio locks for thread-safe operations.
    """

    def __init__(self, bot):
        """
        Initialize the MusicCog.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.queue = {}            # guild_id -> asyncio.Queue of song items
        self.player_messages = {}  # guild_id -> persistent player message
        self.player_channels = {}  # guild_id -> channel to send the persistent message
        self.loop_mode = {}        # guild_id -> bool (True if looping current track)
        self.autoplay_mode = {}    # guild_id -> bool (toggle autoplay)
        self.current_track = {}    # guild_id -> current track (dict)
        self.history = {}          # guild_id -> list of previously played tracks
        self.track_start_time = {} # guild_id -> timestamp when track started
        self.current_source = {}   # guild_id -> reference to the volume transformer
        self.locks = {}            # guild_id -> asyncio.Lock for thread-safe operations

    def get_lock(self, guild_id):
        """
        Get or create an asyncio lock for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            asyncio.Lock: The lock for the guild.
        """
        if guild_id not in self.locks:
            self.locks[guild_id] = asyncio.Lock()
        return self.locks[guild_id]

    async def ensure_voice(self, interaction: nextcord.Interaction):
        """
        Ensure the user is in a voice channel and the bot is connected to it.

        Args:
            interaction (nextcord.Interaction): The interaction object.

        Returns:
            bool: True if the bot is connected to a voice channel, False otherwise.
        """
        if interaction.user.voice is None:
            await interaction.followup.send(f"{interaction.user.display_name}, you are not in a voice channel.", ephemeral=True)
            return False
        channel = interaction.user.voice.channel
        if interaction.guild.voice_client is None:
            await channel.connect()
        elif interaction.guild.voice_client.channel != channel:
            await interaction.guild.voice_client.move_to(channel)
        return True

    # Add rate-limiting to commands
    @nextcord.slash_command(name="play", description="Play a song from YouTube.")
    @cooldown(1, 5, BucketType.guild)
    async def play(self, interaction: nextcord.Interaction, search: str):
        """
        Play a song or add it to the queue.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            query (str): The search query or URL of the song.
        """
        await interaction.response.defer()  # Not ephemeral
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        self.player_channels[guild_id] = interaction.channel

        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "noplaylist": False,
            "cookies": os.getenv("YOUTUBE_COOKIES_PATH", None),
            "geo_bypass": True,
            "nocheckcertificate": True
        }
        result, error_message = await asyncio.to_thread(
            extract_info, search, ydl_opts, os.getenv("YOUTUBE_COOKIES_PATH", None)
        )
        if result is None:
            await interaction.followup.send(f"❌ {error_message}", ephemeral=True)
            return

        async with self.get_lock(guild_id):
            for item in result:
                await self.queue.setdefault(guild_id, asyncio.Queue()).put(item)

            if not interaction.guild.voice_client.is_playing():
                asyncio.create_task(self.play_next(guild_id, interaction))
                await interaction.followup.send("Starting playback...", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"🎵 Added **{result[0]['title']}** to the queue.", 
                    ephemeral=True
                )

    async def play_next(self, guild_id, interaction: nextcord.Interaction = None):
        """
        Play the next track in the queue.

        Args:
            guild_id (int): The ID of the guild.
            interaction (nextcord.Interaction, optional): The interaction object. Defaults to None.
        """
        if guild_id not in self.queue or self.queue[guild_id].empty():
            logger.info("Queue is empty, nothing to play next.")
            if interaction is not None:
                await interaction.followup.send("No more tracks in the queue.", ephemeral=True)
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            if interaction:
                await interaction.followup.send("Bot is not connected to a voice channel.", ephemeral=True)
            return

        item = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, item, interaction)

    async def play_song(self, voice_client, item, interaction: nextcord.Interaction = None):
        """
        Play a specific song.

        Args:
            voice_client (nextcord.VoiceClient): The voice client.
            item (dict): The song item containing information like title, URL, etc.
            interaction (nextcord.Interaction, optional): The interaction object. Defaults to None.
        """
        guild_id = voice_client.guild.id
        self.current_track[guild_id] = item
        self.track_start_time[guild_id] = time.time()

        stream_url = item.get("stream_url")
        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        source = nextcord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        transformer = nextcord.PCMVolumeTransformer(source, volume=0.5)  # Default to 50% volume
        self.current_source[guild_id] = transformer

        def after_callback(error):
            if self.loop_mode.get(guild_id, False):
                self.bot.loop.create_task(self.play_song(voice_client, item))
            else:
                self.bot.loop.create_task(self.play_next(guild_id))
        voice_client.play(transformer, after=after_callback)
        logger.info(f"Now playing: {item.get('title')}")

        # Build the embed with progress bar.
        progress_bar = self.create_progress_bar(0, item.get("duration")) if item.get("duration") else "N/A"
        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=item.get("title"), inline=False)
        embed.add_field(name="⏱ Progress", value=progress_bar, inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({item.get('page_url')})", inline=False)
        embed.set_footer(text=f"Song By: {item.get('artist') or item.get('uploader') or 'Unknown Artist'}")
        if item.get("thumbnail"):
            embed.set_thumbnail(url=item.get("thumbnail"))

        view = MusicControls(self)

        # Delete any previous persistent player message.
        if guild_id in self.player_messages:
            try:
                await self.player_messages[guild_id].delete()
                logger.info("Deleted previous persistent player message.")
                del self.player_messages[guild_id]
            except Exception as e:
                logger.error(f"Error deleting previous player message: {e}")

        # Send a new persistent player message.
        channel = self.player_channels.get(guild_id)
        if not channel:
            channel = voice_client.guild.system_channel or voice_client.guild.text_channels[0]
        try:
            msg = await channel.send(embed=embed, view=view)
            self.player_messages[guild_id] = msg
            logger.info("Sent new persistent player message.")
        except Exception as e:
            logger.error(f"Error sending player message: {e}")

        # Do not edit the original interaction message to avoid duplicate now playing messages.
        if item.get("duration"):
            self.bot.loop.create_task(self.update_now_playing(guild_id, voice_client, item.get("duration")))

    def create_progress_bar(self, elapsed, total, length=20):
        """
        Create a progress bar string.

        Args:
            elapsed (float): The elapsed time in seconds.
            total (float): The total time in seconds.
            length (int, optional): The length of the progress bar. Defaults to 20.

        Returns:
            str: The progress bar string.
        """
        if total <= 0:
            return "N/A"
        progress = min(elapsed / total, 1.0)
        filled_length = int(length * progress)
        bar = "█" * filled_length + "—" * (length - filled_length)
        elapsed_str = self.format_time(elapsed)
        total_str = self.format_time(total)
        return f"{elapsed_str} [{bar}] {total_str}"

    def format_time(self, seconds):
        """
        Format time in seconds to a string.

        Args:
            seconds (float): The time in seconds.

        Returns:
            str: The formatted time string.
        """
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    # Optimize update_now_playing method
    async def update_now_playing(self, guild_id, voice_client, total_duration):
        """
        Update the now playing message for a guild.

        Args:
            guild_id (int): The ID of the guild.
        """
        last_update = 0
        while voice_client.is_playing():
            elapsed = time.time() - self.track_start_time.get(guild_id, time.time())
            if elapsed - last_update >= 5:  # Update every 5 seconds
                progress_bar = self.create_progress_bar(elapsed, total_duration)
                if guild_id in self.player_messages:
                    try:
                        embed = self.player_messages[guild_id].embeds[0]
                        new_embed = nextcord.Embed.from_dict(embed.to_dict())
                        for i, field in enumerate(new_embed.fields):
                            if field.name == "⏱ Progress":
                                new_embed.set_field_at(i, name="⏱ Progress", value=progress_bar, inline=False)
                                break
                        else:
                            new_embed.add_field(name="⏱ Progress", value=progress_bar, inline=False)
                        await self.player_messages[guild_id].edit(embed=new_embed)
                    except Exception as e:
                        logger.error(f"Error updating progress bar: {e}")
                last_update = elapsed
            await asyncio.sleep(1)

    async def download_youtube_audio(self, query):
        """
        Download YouTube audio using youtube_dl.

        Args:
            query (str): The search query or URL.

        Returns:
            list: A list of downloaded audio files.
        """
        cookie_file = os.getenv("YOUTUBE_COOKIES_PATH", None)
        if cookie_file and not os.path.exists(cookie_file):
            logger.warning(f"Cookie file not found: {cookie_file}. Continuing without cookies.")
            cookie_file = None

        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "noplaylist": False,
            "cookies": cookie_file,
            "geo_bypass": True,
            "nocheckcertificate": True
        }
        result = await asyncio.to_thread(extract_info, query, ydl_opts, cookie_file)
        return result

    @nextcord.slash_command(name="remove_track", description="Remove a track from the queue by its position.")
    async def remove_track(self, interaction: nextcord.Interaction, index: int):
        """
        Remove a track from the queue by its position.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            index (int): The position of the track to remove.
        """
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return
        queue_list = list(self.queue[guild_id]._queue)
        if index < 1 or index > len(queue_list):
            await interaction.response.send_message("Invalid track index.", ephemeral=True)
            return
        removed = queue_list.pop(index - 1)
        update_queue(self.queue[guild_id], queue_list)
        await interaction.response.send_message(f"Removed **{removed.get('title')}** from the queue.", ephemeral=True)

    @nextcord.slash_command(name="move_track", description="Move a track to a new position in the queue.")
    async def move_track(self, interaction: nextcord.Interaction, from_index: int, to_index: int):
        """
        Move a track to a new position in the queue.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            from_index (int): The current position of the track.
            to_index (int): The new position for the track.
        """
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return
        queue_list = list(self.queue[guild_id]._queue)
        if from_index < 1 or from_index > len(queue_list) or to_index < 1 or to_index > len(queue_list):
            await interaction.response.send_message("Invalid track indices.", ephemeral=True)
            return
        track = queue_list.pop(from_index - 1)
        queue_list.insert(to_index - 1, track)
        update_queue(self.queue[guild_id], queue_list)
        await interaction.response.send_message(f"Moved **{track.get('title')}** to position {to_index}.", ephemeral=True)

    @nextcord.slash_command(name="queue_details", description="Show detailed queue information.")
    async def queue_details(self, interaction: nextcord.Interaction):
        """
        Show detailed queue information.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return
        queue_list = list(self.queue[guild_id]._queue)
        embed = nextcord.Embed(title="Queue Details", color=nextcord.Color.blue())
        for i, item in enumerate(queue_list, start=1):
            title = item.get("title")
            duration = self.format_time(item.get("duration")) if item.get("duration") else "N/A"
            embed.add_field(name=f"{i}. {title}", value=f"Duration: {duration}\n[Link]({item.get('page_url')})", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def search_youtube(self, query, max_results=5):
        """
        Search YouTube for a query.

        Args:
            query (str): The search query.
            max_results (int, optional): The maximum number of results to return. Defaults to 5.

        Returns:
            list: A list of search results.
        """
        cookie_file = os.getenv("YOUTUBE_COOKIES_PATH", None)
        ydl_opts = {
            "format": "bestaudio",
            "quiet": True,
            "noplaylist": True,
            "cookies": cookie_file,
            "geo_bypass": True,
            "nocheckcertificate": True
        }
        results = []
        def run_search():
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                search_result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                if search_result and "entries" in search_result:
                    for video_info in search_result["entries"]:
                        results.append({
                            "stream_url": video_info.get("url"),
                            "page_url": video_info.get("webpage_url"),
                            "title": video_info.get("title"),
                            "thumbnail": video_info.get("thumbnail"),
                            "artist": video_info.get("artist") or video_info.get("uploader") or "Unknown Artist",
                            "uploader": video_info.get("uploader"),
                            "duration": video_info.get("duration")
                        })
        await asyncio.to_thread(run_search)
        return results

    @nextcord.slash_command(name="search", description="Search for a track and select one to queue.")
    async def search(self, interaction: nextcord.Interaction, query: str):
        """
        Search for a track and select one to queue.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            query (str): The search query.
        """
        await interaction.response.defer(ephemeral=True)
        results = await self.search_youtube(query, max_results=5)
        if not results:
            await interaction.followup.send("No results found.", ephemeral=True)
            return
        view = SearchSelectView(self, results)
        await interaction.followup.send("Select a track to add to the queue:", view=view, ephemeral=True)

    @nextcord.slash_command(name="volume", description="Adjust playback volume (0-150%).")
    async def volume(self, interaction: nextcord.Interaction, volume: int):
        """
        Adjust playback volume.

        Args:
            interaction (nextcord.Interaction): The interaction object.
            volume (int): The volume level (0-150).
        """
        if volume < 0 or volume > 150:
            await interaction.response.send_message("Volume must be between 0 and 150.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None or guild_id not in self.current_source:
            await interaction.response.send_message("No active playback to adjust volume.", ephemeral=True)
            return
        self.current_source[guild_id].volume = volume / 100.0
        await interaction.response.send_message(f"Volume set to {volume}%.", ephemeral=True)

    async def toggle_pause_resume(self, interaction: nextcord.Interaction):
        """
        Toggle pause/resume for the current track.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
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
        """
        Skip the currently playing track.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
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
        """
        Stop the current track and clear the queue.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
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

    async def rewind_track(self, interaction: nextcord.Interaction):
        """
        Rewind the current track (restart).

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
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
        """
        Forward the current track (skip ahead).

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        await self.skip_track(interaction)

    async def replay_track(self, interaction: nextcord.Interaction):
        """
        Replay the current track.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
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

    async def toggle_loop(self, interaction: nextcord.Interaction):
        """
        Toggle loop mode for the current track.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        guild_id = interaction.guild.id
        current = self.loop_mode.get(guild_id, False)
        self.loop_mode[guild_id] = not current
        status = "enabled" if self.loop_mode[guild_id] else "disabled"
        await interaction.response.send_message(f"Loop mode {status}.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """
        Listen to voice state updates.

        Args:
            member (nextcord.Member): The member whose voice state changed.
            before (nextcord.VoiceState): The previous voice state.
            after (nextcord.VoiceState): The new voice state.
        """
        # Act only on the bot's own voice state changes.
        if member.id != self.bot.user.id:
            return

        # When the bot disconnects from a voice channel.
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            if guild_id in self.player_messages:
                try:
                    await self.player_messages[guild_id].delete()
                    del self.player_messages[guild_id]
                    logger.info("Deleted persistent player message because the bot disconnected from voice.")
                except Exception as e:
                    logger.error(f"Error deleting player message on disconnect: {e}")

    # Clean up guild-specific data when bot leaves a guild
    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """
        Clean up guild-specific data when the bot leaves a guild.

        Args:
            guild (nextcord.Guild): The guild that the bot left.
        """
        guild_id = guild.id
        if guild_id in self.queue:
            del self.queue[guild_id]
        if guild_id in self.player_messages:
            del self.player_messages[guild_id]
        if guild_id in self.player_channels:
            del self.player_channels[guild_id]
        if guild_id in self.loop_mode:
            del self.loop_mode[guild_id]
        if guild_id in self.autoplay_mode:
            del self.autoplay_mode[guild_id]
        if guild_id in self.current_track:
            del self.current_track[guild_id]
        if guild_id in self.history:
            del self.history[guild_id]
        if guild_id in self.track_start_time:
            del self.track_start_time[guild_id]
        if guild_id in self.current_source:
            del self.current_source[guild_id]
        if guild_id in self.locks:
            del self.locks[guild_id]

class SearchSelectView(nextcord.ui.View):
    """
    View for selecting a search result.

    Attributes:
        music_cog (Music): The Music cog instance.
        results (list): The list of search results.
    """

    def __init__(self, music_cog, results):
        """
        Initialize the SearchSelectView.

        Args:
            music_cog (Music): The Music cog instance.
            results (list): The list of search results.
        """
        super().__init__(timeout=30)
        self.music_cog = music_cog
        self.results = results
        self.add_item(SearchSelect(self.results))

    async def on_timeout(self):
        """
        Disable all select options when the view times out.
        """
        for child in self.children:
            child.disabled = True

class SearchSelect(nextcord.ui.Select):
    """
    Select menu for choosing a track from search results.

    Attributes:
        results (list): The list of search results.
    """

    def __init__(self, results):
        """
        Initialize the SearchSelect menu.

        Args:
            results (list): The list of search results.
        """
        options = []
        for i, item in enumerate(results):
            label = item.get("title")[:100]
            duration = item.get("duration")
            duration_str = f"Duration: {int(duration//60):02d}:{int(duration%60):02d}" if duration else "N/A"
            options.append(nextcord.SelectOption(label=label, description=duration_str, value=str(i)))
        super().__init__(placeholder="Select a track...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: nextcord.Interaction):
        """
        Handle the selection of a track.

        Args:
            interaction (nextcord.Interaction): The interaction object.
        """
        index = int(self.values[0])
        selected_track = self.view.results[index]
        guild_id = interaction.guild.id
        await self.view.music_cog.queue.setdefault(guild_id, asyncio.Queue()).put(selected_track)
        await interaction.response.send_message(f"Queued **{selected_track.get('title')}**", ephemeral=True)
        if not interaction.guild.voice_client.is_playing():
            self.view.music_cog.bot.loop.create_task(self.view.music_cog.play_next(guild_id, interaction))
        self.view.stop()

def setup(bot):
    """
    Set up the MusicCog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    bot.add_cog(Music(bot))















