import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import logging
import os
import re
import asyncio
import time
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

def extract_info(query, ydl_opts, cookie_file):
    # Quick check for additional services
    if "spotify" in query.lower() or "soundcloud" in query.lower():
        logger.info("Spotify/SoundCloud integration not yet implemented. Please use a YouTube link.")
        return None

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
                    "uploader": video_info.get("uploader"),
                    "duration": video_info.get("duration")
                }]
    return None

# Persistent view for music controls.
class MusicControls(nextcord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @nextcord.ui.button(label="QUEUE", style=nextcord.ButtonStyle.green, custom_id="queue")
    async def queue_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self.cog.queue_details(interaction)

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
        self.track_start_time = {} # guild_id -> timestamp when track started
        self.current_source = {}   # guild_id -> reference to the volume transformer

    async def ensure_voice(self, interaction: nextcord.Interaction):
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
        result = await asyncio.to_thread(
            extract_info, search, ydl_opts, os.getenv("YOUTUBE_COOKIES_PATH", None)
        )
        if result is None:
            await interaction.followup.send("❌ Could not find the video or unsupported service.", ephemeral=True)
            return

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
        guild_id = voice_client.guild.id
        self.current_track[guild_id] = item
        self.track_start_time[guild_id] = time.time()

        stream_url = item.get("stream_url")
        page_url = item.get("page_url")
        title = item.get("title")
        thumbnail = item.get("thumbnail")
        artist = item.get("artist") or item.get("uploader") or "Unknown Artist"
        duration = item.get("duration")  # in seconds

        ffmpeg_opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn"
        }
        source = nextcord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        transformer = nextcord.PCMVolumeTransformer(source)
        self.current_source[guild_id] = transformer

        def after_callback(error):
            if self.loop_mode.get(guild_id, False):
                self.bot.loop.create_task(self.play_song(voice_client, item))
            else:
                self.bot.loop.create_task(self.play_next(guild_id))
        voice_client.play(transformer, after=after_callback)
        logger.info(f"Now playing: {title}")

        # Build the embed with progress bar.
        progress_bar = self.create_progress_bar(0, duration) if duration else "N/A"
        embed = nextcord.Embed(title="🎶 Now Playing", color=nextcord.Color.green())
        embed.add_field(name="🎵 Title", value=title, inline=False)
        embed.add_field(name="⏱ Progress", value=progress_bar, inline=False)
        embed.add_field(name="🔗 URL", value=f"[Click Here]({page_url})", inline=False)
        embed.set_footer(text=f"Song By: {artist}")
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

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
        if duration:
            self.bot.loop.create_task(self.update_now_playing(guild_id, voice_client, duration))

    def create_progress_bar(self, elapsed, total, length=20):
        if total <= 0:
            return "N/A"
        progress = min(elapsed / total, 1.0)
        filled_length = int(length * progress)
        bar = "█" * filled_length + "—" * (length - filled_length)
        elapsed_str = self.format_time(elapsed)
        total_str = self.format_time(total)
        return f"{elapsed_str} [{bar}] {total_str}"

    def format_time(self, seconds):
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    async def update_now_playing(self, guild_id, voice_client, total_duration):
        while voice_client.is_playing():
            elapsed = time.time() - self.track_start_time.get(guild_id, time.time())
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
            await asyncio.sleep(5)

    async def download_youtube_audio(self, query):
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
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.response.send_message("The queue is empty.", ephemeral=True)
            return
        queue_list = list(self.queue[guild_id]._queue)
        if index < 1 or index > len(queue_list):
            await interaction.response.send_message("Invalid track index.", ephemeral=True)
            return
        removed = queue_list.pop(index - 1)
        self.queue[guild_id]._queue.clear()
        for item in queue_list:
            self.queue[guild_id]._queue.append(item)
        await interaction.response.send_message(f"Removed **{removed.get('title')}** from the queue.", ephemeral=True)

    @nextcord.slash_command(name="move_track", description="Move a track to a new position in the queue.")
    async def move_track(self, interaction: nextcord.Interaction, from_index: int, to_index: int):
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
        self.queue[guild_id]._queue.clear()
        for item in queue_list:
            self.queue[guild_id]._queue.append(item)
        await interaction.response.send_message(f"Moved **{track.get('title')}** to position {to_index}.", ephemeral=True)

    @nextcord.slash_command(name="queue_details", description="Show detailed queue information.")
    async def queue_details(self, interaction: nextcord.Interaction):
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
        await interaction.response.defer(ephemeral=True)
        results = await self.search_youtube(query, max_results=5)
        if not results:
            await interaction.followup.send("No results found.", ephemeral=True)
            return
        view = SearchSelectView(self, results)
        await interaction.followup.send("Select a track to add to the queue:", view=view, ephemeral=True)

    @nextcord.slash_command(name="volume", description="Adjust playback volume (0-150%).")
    async def volume(self, interaction: nextcord.Interaction, volume: int):
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

    async def rewind_track(self, interaction: nextcord.Interaction):
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

    async def toggle_loop(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        current = self.loop_mode.get(guild_id, False)
        self.loop_mode[guild_id] = not current
        status = "enabled" if self.loop_mode[guild_id] else "disabled"
        await interaction.response.send_message(f"Loop mode {status}.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
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

class SearchSelectView(nextcord.ui.View):
    def __init__(self, music_cog, results):
        super().__init__(timeout=30)
        self.music_cog = music_cog
        self.results = results
        self.add_item(SearchSelect(self.results))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

class SearchSelect(nextcord.ui.Select):
    def __init__(self, results):
        options = []
        for i, item in enumerate(results):
            label = item.get("title")[:100]
            duration = item.get("duration")
            duration_str = f"Duration: {int(duration//60):02d}:{int(duration%60):02d}" if duration else "N/A"
            options.append(nextcord.SelectOption(label=label, description=duration_str, value=str(i)))
        super().__init__(placeholder="Select a track...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: nextcord.Interaction):
        index = int(self.values[0])
        selected_track = self.view.results[index]
        guild_id = interaction.guild.id
        await self.view.music_cog.queue.setdefault(guild_id, asyncio.Queue()).put(selected_track)
        await interaction.response.send_message(f"Queued **{selected_track.get('title')}**", ephemeral=True)
        if not interaction.guild.voice_client.is_playing():
            self.view.music_cog.bot.loop.create_task(self.view.music_cog.play_next(guild_id, interaction))
        self.view.stop()

def setup(bot):
    bot.add_cog(Music(bot))















