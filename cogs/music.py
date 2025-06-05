# cogs/music.py

"""
MusicCog: A cog for managing music playback and related commands.
This version has been stripped of old hard-coded bits (no load_dotenv, no basicConfig, etc.)
and now expects all configuration (like YOUTUBE_COOKIES_PATH) to be set in your .env or Config.
"""

import os
import re
import json
import time
import random
import asyncio
import logging

import yt_dlp as youtube_dl
import nextcord
from nextcord.ext import commands
from nextcord.ext.commands import cooldown, BucketType

# Module-level logger (no basicConfig here)
logger = logging.getLogger("elbot.music")

# If you want to persist queues across restarts, this file will be created in your working directory.
QUEUE_FILE = "queue.json"


def extract_info(query: str, ydl_opts: dict, cookie_file: str):
    """
    Extract video information from a query using youtube_dl.
    Returns a list of track dicts or (None, error_message).
    """
    # Quick check for unsupported services
    if "spotify" in query.lower() or "soundcloud" in query.lower():
        logger.info("Spotify/SoundCloud integration not implemented. Use a YouTube link.")
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


def update_queue(queue: asyncio.Queue, new_items: list):
    """
    Replace all items in `queue` with `new_items`.
    """
    queue._queue.clear()
    for item in new_items:
        queue._queue.append(item)


class MusicControls(nextcord.ui.View):
    """
    Persistent View for music controls. All buttons call methods on the Music cog.
    """

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @nextcord.ui.button(label="QUEUE", style=nextcord.ButtonStyle.green, custom_id="queue")
    async def queue_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.queue_details(interaction)

    @nextcord.ui.button(label="⏯ Pause/Resume", style=nextcord.ButtonStyle.grey, custom_id="pause_resume")
    async def pause_resume_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.toggle_pause_resume(interaction)

    @nextcord.ui.button(label="SKIP", style=nextcord.ButtonStyle.green, custom_id="skip")
    async def skip_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.skip_track(interaction)

    @nextcord.ui.button(label="REWIND", style=nextcord.ButtonStyle.green, custom_id="rewind")
    async def rewind_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.rewind_track(interaction)

    @nextcord.ui.button(label="FORWARD", style=nextcord.ButtonStyle.green, custom_id="forward")
    async def forward_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.forward_track(interaction)

    @nextcord.ui.button(label="REPLAY", style=nextcord.ButtonStyle.green, custom_id="replay")
    async def replay_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.replay_track(interaction)

    @nextcord.ui.button(label="LOOP", style=nextcord.ButtonStyle.green, custom_id="loop")
    async def loop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.toggle_loop(interaction)

    @nextcord.ui.button(label="🛑 STOP", style=nextcord.ButtonStyle.red, custom_id="stop")
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        self.cog.update_activity(interaction.guild.id)
        await self.cog.stop_track(interaction)


class Music(commands.Cog):
    """
    A cog for managing music playback and related commands.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue = {}             # guild_id -> asyncio.Queue
        self.player_messages = {}   # guild_id -> persistent player message
        self.player_channels = {}   # guild_id -> channel for persistent message
        self.loop_mode = {}         # guild_id -> bool
        self.autoplay_mode = {}     # guild_id -> bool
        self.current_track = {}     # guild_id -> dict
        self.history = {}           # guild_id -> list
        self.track_start_time = {}  # guild_id -> timestamp
        self.current_source = {}    # guild_id -> PCMVolumeTransformer
        self.locks = {}             # guild_id -> asyncio.Lock
        self.last_activity = {}     # guild_id -> last activity timestamp
        self.timeout_tasks = {}     # guild_id -> asyncio.Task

        # Limit queue size to avoid runaway memory usage
        self.max_queue_size = 50

        # Load any previously saved queues
        self.load_queue()

        # Launch timeout checkers for already-joined guilds
        for guild in bot.guilds:
            self.start_timeout_checker(guild.id)

        # Limit concurrent YouTube-DL extractions
        self.download_semaphore = asyncio.Semaphore(4)

    def cog_unload(self):
        """Cancel all timeout checker tasks when the cog is unloaded."""
        for task in self.timeout_tasks.values():
            task.cancel()

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        """
        Get or create an asyncio.Lock for a guild to synchronize queue operations.
        """
        if guild_id not in self.locks:
            self.locks[guild_id] = asyncio.Lock()
        return self.locks[guild_id]

    async def ensure_voice(self, interaction: nextcord.Interaction) -> bool:
        """
        Ensure the user is in a voice channel and the bot is connected to it.
        """
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
    @cooldown(1, 5, BucketType.guild)
    async def play(self, interaction: nextcord.Interaction, search: str):
        """
        Play a song or add it to the queue.
        """
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        self.player_channels[guild_id] = interaction.channel
        self.update_activity(guild_id)

        # Build youtube_dl options
        ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": False,
            "cookies": os.getenv("YOUTUBE_COOKIES_PATH", None),
            "geo_bypass": True,
            "nocheckcertificate": True,
            "buffersize": 512,
        }

        if len(search) > 200:
            await interaction.followup.send("❌ Search query too long.", ephemeral=True)
            return

        # Throttle YouTube-DL extraction
        async with self.download_semaphore:
            result, error = await asyncio.to_thread(
                extract_info, search, ydl_opts, os.getenv("YOUTUBE_COOKIES_PATH", None)
            )

        if result is None:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return

        async with self.get_lock(guild_id):
            current_queue = self.queue.setdefault(guild_id, asyncio.Queue())
            if current_queue.qsize() >= self.max_queue_size:
                await interaction.followup.send(
                    f"❌ Queue is full! Max {self.max_queue_size} songs.", 
                    ephemeral=True
                )
                return

            for item in result:
                await current_queue.put(item)

            voice_client = interaction.guild.voice_client
            if not voice_client.is_playing():
                asyncio.create_task(self.play_next(guild_id, interaction))
                await interaction.followup.send("▶️ Starting playback...", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"➕ Added **{result[0]['title']}** to the queue.", 
                    ephemeral=True
                )

    async def play_next(self, guild_id: int, interaction: nextcord.Interaction = None):
        """
        Pop the next track from the queue and play it.
        """
        if guild_id not in self.queue or self.queue[guild_id].empty():
            logger.info("Queue is empty.")
            if interaction:
                await interaction.followup.send("No more tracks in the queue.", ephemeral=True)

            # Remove the persistent player message after a short delay
            if guild_id in self.player_messages:
                await asyncio.sleep(10)
                try:
                    await self.player_messages[guild_id].delete()
                    del self.player_messages[guild_id]
                    logger.info("Removed persistent player message.")
                except Exception as e:
                    logger.error(f"Error removing player message: {e}")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            if interaction:
                await interaction.followup.send(
                    "Bot is not connected to a voice channel.", ephemeral=True
                )
            return

        item = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, item, interaction)
        await self.save_queue()

    async def play_song(self, voice_client: nextcord.VoiceClient, item: dict, interaction: nextcord.Interaction = None):
        """
        Actually begin playback of a single track.
        """
        guild_id = voice_client.guild.id
        self.current_track[guild_id] = item
        self.track_start_time[guild_id] = time.time()

        stream_url = item["stream_url"]
        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn -threads 1 -bufsize 512k -ab 64k"
        }
        source = nextcord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        transformer = nextcord.PCMVolumeTransformer(source, volume=0.5)
        self.current_source[guild_id] = transformer

        def after_callback(error):
            if error:
                logger.error(f"Playback error in guild {guild_id}: {error}")
            if self.loop_mode.get(guild_id, False):
                self.bot.loop.create_task(self.play_song(voice_client, item))
            else:
                self.bot.loop.create_task(self.play_next(guild_id))

        voice_client.play(transformer, after=after_callback)
        logger.info(f"Now playing: {item['title']}")

        # Build the “Now Playing” embed with a progress bar
        progress_bar = (
            self.create_progress_bar(0, item["duration"]) if item.get("duration") else "N/A"
        )
        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=item["title"], inline=False)
        embed.add_field(name="⏱ Progress", value=progress_bar, inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({item['page_url']})", inline=False)
        embed.set_footer(text=f"Song By: {item.get('artist') or item.get('uploader') or 'Unknown Artist'}")
        if item.get("thumbnail"):
            embed.set_thumbnail(url=item["thumbnail"])

        view = MusicControls(self)

        # Edit existing persistent message, or send a new one
        if guild_id in self.player_messages:
            try:
                await self.player_messages[guild_id].edit(embed=embed, view=view)
                logger.info("Updated persistent player message.")
            except Exception as e:
                logger.error(f"Error updating player message: {e}")
        else:
            channel = self.player_channels.get(guild_id) or (
                voice_client.guild.system_channel or voice_client.guild.text_channels[0]
            )
            try:
                msg = await channel.send(embed=embed, view=view)
                self.player_messages[guild_id] = msg
                logger.info("Sent new persistent player message.")
            except Exception as e:
                logger.error(f"Error sending player message: {e}")

        # Schedule periodic “Now Playing” updates if we know the track duration
        if item.get("duration"):
            self.bot.loop.create_task(self.update_now_playing(guild_id, voice_client, item["duration"]))

    def create_progress_bar(self, elapsed: float, total: float, length: int = 20) -> str:
        """
        Return a string progress bar given elapsed and total seconds.
        """
        if total <= 0:
            return "N/A"
        progress = min(elapsed / total, 1.0)
        filled_length = int(length * progress)
        bar = "█" * filled_length + "—" * (length - filled_length)
        elapsed_str = self.format_time(elapsed)
        total_str = self.format_time(total)
        return f"{elapsed_str} [{bar}] {total_str}"

    def format_time(self, seconds: float) -> str:
        """
        Convert a number of seconds into MM:SS format.
        """
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    async def update_now_playing(self, guild_id: int, voice_client: nextcord.VoiceClient, total_duration: float):
        """
        Periodically edit the “Now Playing” message’s progress bar.
        Updates every 20 seconds to reduce load.
        """
        last_update = 0
        while voice_client.is_playing():
            elapsed = time.time() - self.track_start_time.get(guild_id, time.time())
            if elapsed - last_update >= 20:
                progress_bar = self.create_progress_bar(elapsed, total_duration)
                if guild_id in self.player_messages:
                    try:
                        embed = self.player_messages[guild_id].embeds[0]
                        embed.set_field_at(1, name="⏱ Progress", value=progress_bar, inline=False)
                        await self.player_messages[guild_id].edit(embed=embed)
                        last_update = elapsed
                    except Exception as e:
                        logger.error(f"Failed to update now playing message: {e}")
                        break
            await asyncio.sleep(10)

    @nextcord.slash_command(name="remove_track", description="Remove a track from the queue by its position.")
    async def remove_track(self, interaction: nextcord.Interaction, index: int):
        """
        Remove a track from the queue at position `index` (1-based).
        """
        guild_id = interaction.guild.id
        self.update_activity(guild_id)

        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        queue_list = list(self.queue[guild_id]._queue)
        if index < 1 or index > len(queue_list):
            await interaction.response.send_message("Invalid track index.", ephemeral=True)
            return

        removed = queue_list.pop(index - 1)
        update_queue(self.queue[guild_id], queue_list)
        await interaction.response.send_message(f"Removed **{removed['title']}** from the queue.", ephemeral=True)

    @nextcord.slash_command(name="move_track", description="Move a track to a new position in the queue.")
    async def move_track(self, interaction: nextcord.Interaction, from_index: int, to_index: int):
        """
        Move a track from `from_index` to `to_index` (1-based).
        """
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return

        queue_list = list(self.queue[guild_id]._queue)
        if (from_index < 1 or from_index > len(queue_list)
                or to_index < 1 or to_index > len(queue_list)):
            await interaction.response.send_message("Invalid track indices.", ephemeral=True)
            return

        track = queue_list.pop(from_index - 1)
        queue_list.insert(to_index - 1, track)
        update_queue(self.queue[guild_id], queue_list)
        await interaction.response.send_message(f"Moved **{track['title']}** to position {to_index}.", ephemeral=True)

    @nextcord.slash_command(name="queue_details", description="Show detailed queue information.")
    async def queue_details(self, interaction: nextcord.Interaction):
        """
        Display the full queue in an embed.
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
            embed.add_field(
                name=f"{i}. {title}",
                value=f"Duration: {duration}\n[Link]({item.get('page_url')})",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def search_youtube(self, query: str, max_results: int = 5) -> list:
        """
        Perform a YouTube search and return up to `max_results` items.
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
        Let user pick a track from search results.
        """
        await interaction.response.defer(ephemeral=True)
        results = await self.search_youtube(query, max_results=5)
        if not results:
            await interaction.followup.send("No results found.", ephemeral=True)
            return

        view = SearchSelectView(self, results)
        await interaction.followup.send(
            "Select a track to add to the queue:", view=view, ephemeral=True
        )

    @nextcord.slash_command(name="volume", description="Adjust playback volume (0-150%).")
    async def volume(self, interaction: nextcord.Interaction, volume: int):
        """
        Change the playback volume (0–150).
        """
        if volume < 0 or volume > 150:
            await interaction.response.send_message(
                "Volume must be between 0 and 150.", ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None or guild_id not in self.current_source:
            await interaction.response.send_message(
                "No active playback to adjust volume.", ephemeral=True
            )
            return

        self.current_source[guild_id].volume = volume / 100.0
        await interaction.response.send_message(f"Volume set to {volume}%.", ephemeral=True)

    async def toggle_pause_resume(self, interaction: nextcord.Interaction):
        """
        Pause or resume the current track.
        """
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message(
                "Not connected to a voice channel.", ephemeral=True
            )
            return

        if voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("▶️ Resumed playback.", ephemeral=True)
        elif voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸ Paused playback.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    async def skip_track(self, interaction: nextcord.Interaction):
        """
        Skip the currently playing track.
        """
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message(
                "Not connected to a voice channel.", ephemeral=True
            )
            return

        async with self.get_lock(guild_id):
            if guild_id in self.current_track:
                self.history.setdefault(guild_id, []).append(self.current_track[guild_id])
            voice_client.stop()
        await interaction.response.send_message("⏭ Skipped track.", ephemeral=True)

    async def stop_track(self, interaction: nextcord.Interaction):
        """
        Stop playback and clear the entire queue.
        """
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message(
                "Not connected to a voice channel.", ephemeral=True
            )
            return

        async with self.get_lock(guild_id):
            voice_client.stop()
            if guild_id in self.queue:
                while not self.queue[guild_id].empty():
                    try:
                        self.queue[guild_id].get_nowait()
                    except asyncio.QueueEmpty:
                        break

        await interaction.response.send_message("🛑 Stopped and cleared queue.", ephemeral=True)

    async def rewind_track(self, interaction: nextcord.Interaction):
        """
        Rewind the current track to the start.
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
        await interaction.followup.send("⏪ Rewound the track.", ephemeral=True)

    async def forward_track(self, interaction: nextcord.Interaction):
        """
        Simply skip the current track (alias of skip).
        """
        await self.skip_track(interaction)

    async def replay_track(self, interaction: nextcord.Interaction):
        """
        Replay the current track from the beginning.
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
        await interaction.followup.send("🔁 Replaying the track.", ephemeral=True)

    async def toggle_loop(self, interaction: nextcord.Interaction):
        """
        Toggle loop mode for the currently playing track.
        """
        guild_id = interaction.guild.id
        current = self.loop_mode.get(guild_id, False)
        self.loop_mode[guild_id] = not current
        status = "enabled" if self.loop_mode[guild_id] else "disabled"
        await interaction.response.send_message(f"↩️ Loop mode {status}.", ephemeral=True)

    @commands.command(name="shuffle")
    async def shuffle_queue(self, ctx: commands.Context):
        """
        Shuffle the current queue (text-command version).
        """
        guild_id = ctx.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await ctx.send("The queue is empty! Nothing to shuffle.")
            return

        random.shuffle(self.queue[guild_id]._queue)
        await ctx.send("🔀 The queue has been shuffled!")

    @commands.command(name="reorder")
    async def reorder_queue(self, ctx: commands.Context, old_index: int, new_index: int):
        """
        Reorder a song in the queue by index (text-command version).
        """
        guild_id = ctx.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await ctx.send("The queue is empty! Nothing to reorder.")
            return

        queue_list = self.queue[guild_id]._queue
        if (old_index < 1 or new_index < 1 or
                old_index > len(queue_list) or new_index > len(queue_list)):
            await ctx.send("Invalid indices! Use valid positions in the queue.")
            return

        song = queue_list.pop(old_index - 1)
        queue_list.insert(new_index - 1, song)
        await ctx.send(f"🔄 Moved song to position {new_index}.")

    @commands.command(name="save_playlist")
    async def save_playlist(self, ctx: commands.Context, name: str):
        """
        Save the current queue to a JSON file named `<name>.json`.
        """
        guild_id = ctx.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await ctx.send("The queue is empty! Nothing to save.")
            return

        playlist = list(self.queue[guild_id]._queue)
        with open(f"{name}.json", "w") as f:
            json.dump(playlist, f)
        await ctx.send(f"💾 Playlist '{name}' has been saved.")

    @commands.command(name="load_playlist")
    async def load_playlist(self, ctx: commands.Context, name: str):
        """
        Load a JSON playlist (`<name>.json`) into the queue.
        """
        guild_id = ctx.guild.id
        try:
            with open(f"{name}.json", "r") as f:
                playlist = json.load(f)
            update_queue(self.queue.setdefault(guild_id, asyncio.Queue()), playlist)
            await ctx.send(f"📂 Playlist '{name}' has been loaded into the queue.")
        except FileNotFoundError:
            await ctx.send(f"❌ Playlist '{name}' not found.")

    async def save_queue(self):
        """
        Persist all guild queues to disk as JSON.
        """
        data = {}
        for guild_id, q in self.queue.items():
            data[str(guild_id)] = list(q._queue)
        tmp_file = QUEUE_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(data, f)
        os.replace(tmp_file, QUEUE_FILE)

    def load_queue(self):
        """
        Load persisted queues from disk into asyncio.Queues.
        """
        try:
            with open(QUEUE_FILE, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            return

        for guild_id_str, items in data.items():
            try:
                gid = int(guild_id_str)
                q = asyncio.Queue()
                for it in items:
                    q.put_nowait(it)
                self.queue[gid] = q
            except Exception:
                continue

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: nextcord.Guild):
        """
        Clean up all guild-specific data when the bot is removed from a guild.
        """
        guild_id = guild.id
        keys = [
            "queue", "player_messages", "player_channels", "loop_mode",
            "autoplay_mode", "current_track", "history", "track_start_time",
            "current_source", "locks", "last_activity", "timeout_tasks"
        ]
        for key in keys:
            container = getattr(self, key)
            if guild_id in container:
                del container[guild_id]

    async def check_timeout_conditions(self, guild_id: int) -> bool:
        """
        Return True if the bot should disconnect from voice due to inactivity or being alone.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return True

        voice_client = guild.voice_client

        # If no non-bot members remain in channel, timeout
        non_bot_members = [m for m in voice_client.channel.members if not m.bot]
        if len(non_bot_members) == 0:
            return True

        # If > 5 minutes of inactivity, timeout
        last = self.last_activity.get(guild_id, 0)
        if time.time() - last > 300:  # 300 seconds = 5 minutes
            return True

        return False

    def update_activity(self, guild_id: int):
        """
        Update the last activity timestamp for a guild.
        """
        self.last_activity[guild_id] = time.time()

    def start_timeout_checker(self, guild_id: int):
        """
        Begin a background task that checks every minute if the bot should disconnect.
        """
        async def timeout_checker():
            while True:
                try:
                    if await self.check_timeout_conditions(guild_id):
                        guild = self.bot.get_guild(guild_id)
                        if guild and guild.voice_client:
                            logger.info(f"Auto-disconnecting from {guild.name} due to timeout.")
                            await guild.voice_client.disconnect()

                            # Delete persistent player message if it exists
                            if guild_id in self.player_messages:
                                try:
                                    await self.player_messages[guild_id].delete()
                                except:
                                    pass
                                del self.player_messages[guild_id]

                            # Clear the queue to free memory
                            if guild_id in self.queue:
                                while not self.queue[guild_id].empty():
                                    try:
                                        self.queue[guild_id].get_nowait()
                                    except asyncio.QueueEmpty:
                                        break

                        break
                    await asyncio.sleep(60)  # Check once per minute
                except Exception as e:
                    logger.error(f"Error in timeout checker: {e}")
                    await asyncio.sleep(60)

        if guild_id in self.timeout_tasks:
            self.timeout_tasks[guild_id].cancel()
        self.timeout_tasks[guild_id] = asyncio.create_task(timeout_checker())


class SearchSelectView(nextcord.ui.View):
    """
    View for selecting a track from search results.
    """

    def __init__(self, music_cog: Music, results: list):
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
    """

    def __init__(self, results: list):
        options = []
        for i, item in enumerate(results):
            label = item.get("title")[:100]
            duration = item.get("duration")
            dur_str = (
                f"Duration: {int(duration // 60):02d}:{int(duration % 60):02d}" 
                if duration else "N/A"
            )
            options.append(nextcord.SelectOption(label=label, description=dur_str, value=str(i)))

        super().__init__(placeholder="Select a track...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: nextcord.Interaction):
        """
        When a user selects a track, queue it and start playback if nothing is playing.
        """
        index = int(self.values[0])
        selected = self.view.results[index]
        guild_id = interaction.guild.id

        await self.view.music_cog.queue.setdefault(guild_id, asyncio.Queue()).put(selected)
        await interaction.response.send_message(f"✅ Queued **{selected['title']}**", ephemeral=True)

        if not interaction.guild.voice_client.is_playing():
            self.view.music_cog.bot.loop.create_task(
                self.view.music_cog.play_next(guild_id, interaction)
            )

        self.view.stop()


def setup(bot: commands.Bot):
    """
    Called by the bot to register this cog.
    """
    bot.add_cog(Music(bot))

    # Optional: “moan” slash command for a sound effect
    @nextcord.slash_command(name="moan", description="Play a moan sound effect")
    @cooldown(1, 5, BucketType.user)
    async def moan(interaction: nextcord.Interaction):
        """
        Play a moan sound file from /sounds/56753004_girl-moaning_by_a-sfx_preview.mp3.
        """
        if not interaction.user.voice:
            await interaction.response.send_message(
                "You need to be in a voice channel to use this command!", ephemeral=True
            )
            return

        # Path to the sound file (project_root/sounds/...)
        sound_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "sounds", 
            "56753004_girl-moaning_by_a-sfx_preview.mp3"
        )

        if not os.path.exists(sound_path):
            await interaction.response.send_message(
                "❌ Sound effect not found! Contact the bot administrator.", ephemeral=True
            )
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            try:
                channel = interaction.user.voice.channel
                await channel.connect()
                voice_client = interaction.guild.voice_client
            except Exception as e:
                await interaction.response.send_message(
                    f"Could not connect to voice channel: {str(e)}", ephemeral=True
                )
                return

        if voice_client.is_playing():
            voice_client.stop()

        try:
            voice_client.play(nextcord.FFmpegPCMAudio(sound_path))
            await interaction.response.send_message("😏", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error playing sound: {str(e)}", ephemeral=True)
import os
import time 