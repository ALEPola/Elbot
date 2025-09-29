from __future__ import annotations

import asyncio
import io
import logging
import random

import nextcord
from nextcord import SlashOption
from nextcord.ext import commands

from elbot.cogs.chat import openai_client
from elbot.utils import safe_reply

logger = logging.getLogger("elbot.speech")

VOICE_OPTIONS = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
]


class SpeechCog(commands.Cog):
    """A cog that speaks user supplied text via OpenAI TTS."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @nextcord.slash_command(
        name="speech",
        description="Speak your message aloud using OpenAI text-to-speech.",
    )
    async def speech(
        self,
        interaction: nextcord.Interaction,
        message: str = SlashOption(
            name="message",
            description="What should I say?",
            required=True,
        ),
    ) -> None:
        await interaction.response.defer()

        if not openai_client:
            await safe_reply(
                interaction,
                "⚠️ Speech functionality is not available right now.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, nextcord.Member) or user.voice is None:
            await safe_reply(
                interaction,
                "You must join a voice channel first.",
                ephemeral=True,
            )
            return

        voice_name = random.choice(VOICE_OPTIONS)

        voice_client = guild.voice_client
        joined_here = False
        try:
            target_channel = user.voice.channel
            if voice_client is None or voice_client.channel != target_channel:
                if voice_client is not None:
                    try:
                        await voice_client.disconnect(force=True)
                    except Exception:  # pragma: no cover - best effort cleanup
                        pass
                voice_client = await target_channel.connect()
                joined_here = True
        except Exception as exc:  # pragma: no cover - discord connection errors
            logger.error("Failed to join voice channel: %s", exc, exc_info=True)
            await safe_reply(
                interaction,
                "Could not join your voice channel.",
                ephemeral=True,
            )
            return

        try:
            audio_buffer = await asyncio.to_thread(
                self._synthesize_speech,
                message,
                voice_name,
            )
        except Exception as exc:
            logger.error("OpenAI TTS request failed: %s", exc, exc_info=True)
            await safe_reply(
                interaction,
                f"⚠️ TTS failed: {exc}",
                ephemeral=True,
            )
            if joined_here and voice_client:
                try:
                    await voice_client.disconnect()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
            return

        ffmpeg_options = {"options": "-loglevel quiet"}
        source = nextcord.FFmpegPCMAudio(audio_buffer, pipe=True, **ffmpeg_options)

        if voice_client.is_playing():
            voice_client.stop()

        done_event = asyncio.Event()
        loop = self.bot.loop

        def after_playback(error: Exception | None) -> None:
            if error:
                logger.error("Voice playback failed: %s", error)
            loop.call_soon_threadsafe(done_event.set)

        voice_client.play(source, after=after_playback)
        await done_event.wait()

        if joined_here and voice_client:
            try:
                await voice_client.disconnect()
            except Exception:  # pragma: no cover - best effort cleanup
                pass

        await safe_reply(
            interaction,
            f"🗣️ Spoke your message using the **{voice_name}** voice.",
        )

    @staticmethod
    def _synthesize_speech(message: str, voice_name: str) -> io.BytesIO:
        with openai_client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            input=message,
            voice=voice_name,
            response_format="mp3",
        ) as stream:
            audio_buffer = io.BytesIO()
            for chunk in stream.iter_bytes():
                audio_buffer.write(chunk)
        audio_buffer.seek(0)
        return audio_buffer


def setup(bot: commands.Bot) -> None:
    bot.add_cog(SpeechCog(bot))

