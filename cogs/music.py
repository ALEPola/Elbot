import nextcord
from nextcord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv
from nextcord.ui import View, Button

# Load environment variables
load_dotenv()


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # Use an asyncio.Queue for each guild

    async def ensure_voice(self, interaction):
        """Ensures the bot is in the same voice channel as the user."""
        if interaction.user.voice is None:
            await interaction.followup.send("You must be in a voice channel!", ephemeral=True)
            return False

        channel = interaction.user.voice.channel
        if interaction.guild.voice_client is None:
            await channel.connect()
        elif interaction.guild.voice_client.channel != channel:
            await interaction.guild.voice_client.move_to(channel)

        return True

    async def play_next(self, guild_id):
        """Plays the next song in the queue."""
        if guild_id not in self.queue or self.queue[guild_id].empty():
            return

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            return

        url, title = await self.queue[guild_id].get()
        await self.play_song(guild.voice_client, url, title, guild)

    async def play_song(self, voice_client, url, title, guild):
        """Plays a song and displays interactive controls."""
        ffmpeg_opts = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        source = nextcord.FFmpegPCMAudio(url, **ffmpeg_opts)
        voice_client.play(
            nextcord.PCMVolumeTransformer(source),
            after=lambda _: self.bot.loop.create_task(self.play_next(voice_client.guild.id))
        )

        embed = nextcord.Embed(
            title="🎵 Now Playing",
            description=f"**[{title}]({url})**",
            color=nextcord.Color.blue()
        )
        embed.set_footer(text="Use the buttons below to control playback.")

        view = MusicControls(self, guild)
        await guild.text_channels[0].send(embed=embed, view=view)

    async def download_youtube_audio(self, query):
        """Downloads the best audio format from YouTube."""
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'noplaylist': False,
            'cookies': os.getenv('YOUTUBE_COOKIES_PATH', '/home/alex/Documents/youtube_cookies.txt'),
        }

        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                video_info = ydl.extract_info(query, download=False)
                return [{"url": video_info["url"], "title": video_info["title"]}]
        except youtube_dl.DownloadError as e:
            return None

    @nextcord.slash_command(name="play", description="Play a song from YouTube.")
    async def play(self, interaction: nextcord.Interaction, search: str):
        await interaction.response.defer()
        if not await self.ensure_voice(interaction):
            return

        guild_id = interaction.guild.id
        if guild_id not in self.queue:
            self.queue[guild_id] = asyncio.Queue()

        result = await self.download_youtube_audio(search)
        if result is None:
            await interaction.followup.send("Could not find the video.")
            return

        for item in result:
            await self.queue[guild_id].put((item["url"], item["title"]))

        if not interaction.guild.voice_client.is_playing():
            await self.play_next(guild_id)
        else:
            await interaction.followup.send(f"Added **{result[0]['title']}** to the queue.")

    @nextcord.slash_command(name="queue", description="Displays the current queue.")
    async def queue(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)

        queue_list = "\n".join([f"{index + 1}. {song[1]}" for index, song in enumerate(self.queue[guild_id]._queue)])
        embed = nextcord.Embed(
            title="🎶 Music Queue",
            description=queue_list,
            color=nextcord.Color.gold()
        )
        embed.set_footer(text="Use buttons to manage the queue.")

        view = QueueControls(self, interaction)
        await interaction.response.send_message(embed=embed, view=view)

    @nextcord.slash_command(name="skip", description="Skip the currently playing song.")
    async def skip(self, interaction: nextcord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("Skipped the song.")
        else:
            await interaction.response.send_message("No song is currently playing.", ephemeral=True)

    @nextcord.slash_command(name="play_song", description="Play a specific song from the queue by number.")
    async def play_song_by_index(self, interaction: nextcord.Interaction, song_number: int):
        guild_id = interaction.guild.id
        if guild_id not in self.queue or song_number < 1 or song_number > len(self.queue[guild_id]._queue):
            return await interaction.response.send_message("Invalid song number.", ephemeral=True)

        song = self.queue[guild_id]._queue.pop(song_number - 1)
        self.queue[guild_id]._queue.insert(0, song)  # Move song to front
        await interaction.response.send_message(f"Playing: **{song[1]}** next!")
        interaction.guild.voice_client.stop()  # Skip to trigger play_next()


class MusicControls(View):
    def __init__(self, music_cog, guild):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.guild = guild

    @nextcord.ui.button(label="⏸ Pause", style=nextcord.ButtonStyle.gray)
    async def pause(self, button: Button, interaction: nextcord.Interaction):
        if self.guild.voice_client.is_playing():
            self.guild.voice_client.pause()
            await interaction.response.send_message("Paused the song.")

    @nextcord.ui.button(label="▶ Resume", style=nextcord.ButtonStyle.green)
    async def resume(self, button: Button, interaction: nextcord.Interaction):
        if self.guild.voice_client.is_paused():
            self.guild.voice_client.resume()
            await interaction.response.send_message("Resumed the song.")

    @nextcord.ui.button(label="⏭ Next", style=nextcord.ButtonStyle.blurple)
    async def skip(self, button: Button, interaction: nextcord.Interaction):
        await self.music_cog.skip(interaction)


class QueueControls(View):
    def __init__(self, music_cog, interaction):
        super().__init__(timeout=None)
        self.music_cog = music_cog
        self.interaction = interaction

    @nextcord.ui.button(label="❌ Clear Queue", style=nextcord.ButtonStyle.red)
    async def clear(self, button: Button, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        self.music_cog.queue[guild_id] = asyncio.Queue()
        await interaction.response.send_message("The queue has been cleared.")


def setup(bot):
    bot.add_cog(Music(bot))



