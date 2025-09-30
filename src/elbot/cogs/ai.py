"""Unified AI cog bundling chat, image generation, and voice helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Deque, Dict, Iterable, Tuple

import nextcord
from nextcord import SlashOption
from nextcord.ext import commands
from openai import OpenAI

from elbot.config import Config
from elbot.utils import safe_reply

logger = logging.getLogger("elbot.ai")

openai_client: OpenAI | None = None
OPENAI_MODEL = Config.OPENAI_MODEL
RATE_LIMIT_SECONDS = 5
MAX_RESPONSE_LENGTH = 2000
HISTORY_LEN = 5
HISTORY_TTL_SECONDS = 600


def _ensure_openai_client() -> OpenAI | None:
    """Return a shared OpenAI client, initialising it on first use."""

    global openai_client
    if openai_client is not None:
        return openai_client

    if not Config.OPENAI_API_KEY:
        logger.warning("OpenAI API key missing; AI commands disabled")
        return None

    try:
        openai_client = OpenAI(api_key=Config.OPENAI_API_KEY)
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("Failed to initialise OpenAI client")
        return None

    return openai_client


def _allow_request(
    cache: Dict[int, float], user_id: int, *, rate_limit: int = RATE_LIMIT_SECONDS
) -> Tuple[bool, float]:
    """Return whether the user may issue a new request under the rate limit."""

    now = time.monotonic()
    last = cache.get(user_id, 0.0)
    if now - last < rate_limit:
        return False, now
    cache[user_id] = now
    return True, now


def _trim_history(history: Deque[Tuple[float, str, str]], *, now: float) -> None:
    """Drop expired history entries based on ``HISTORY_TTL_SECONDS``."""

    while history and now - history[0][0] > HISTORY_TTL_SECONDS:
        history.popleft()


class AICog(commands.Cog):
    """Collect chat, image, and voice helpers under a single ``/ai`` group."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._user_last_interaction: Dict[int, float] = {}
        self._histories: Dict[int, Deque[Tuple[float, str, str]]] = defaultdict(deque)
        self._history_dir = Path(Config.BASE_DIR) / "chat_history"
        self._history_dir.mkdir(exist_ok=True)
        self._disabled_voice_guilds: set[int] = set()

    # ------------------------------------------------------------------
    # Chat helpers
    # ------------------------------------------------------------------
    def _persist_history(self, user_id: int, role: str, content: str) -> None:
        file = self._history_dir / f"{user_id}.json"
        try:
            if file.exists():
                data = json.loads(file.read_text(encoding="utf-8"))
            else:
                data = []
        except Exception:
            data = []
        data.append({"ts": time.time(), "role": role, "content": content})
        with file.open("w", encoding="utf-8") as f:
            json.dump(data, f)

    def _load_history(self, user_id: int) -> list:
        file = self._history_dir / f"{user_id}.json"
        if not file.exists():
            return []
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            return []

    async def _run_chat_completion(self, messages: Iterable[dict]) -> str:
        client = _ensure_openai_client()
        if not client:
            logger.warning("OpenAI client not configured; cannot generate reply")
            return "Sorry, chat functionality is not available right now."

        try:
            completion = await asyncio.to_thread(
                lambda: client.chat.completions.create(model=OPENAI_MODEL, messages=list(messages))
            )
            content = completion.choices[0].message.content
        except Exception:
            logger.error("OpenAI error while generating response.", exc_info=True)
            return "Sorry, something went wrong with the chat bot."

        if len(content) > MAX_RESPONSE_LENGTH:
            return content[: MAX_RESPONSE_LENGTH - 3] + "..."
        return content

    async def _handle_chat(self, interaction: nextcord.Interaction, message: str) -> None:
        await interaction.response.defer(with_message=True)
        user_id = interaction.user.id
        allowed, now = _allow_request(self._user_last_interaction, user_id)
        if not allowed:
            await safe_reply(
                interaction,
                "Please wait a few seconds before chatting again.",
                ephemeral=True,
            )
            return

        text = message.strip()
        from textblob import TextBlob

        sentiment = TextBlob(text).sentiment
        logger.info("User %s sentiment: %s", user_id, sentiment)
        if sentiment.polarity < -0.5:
            await safe_reply(
                interaction,
                "It seems like you're upset. How can I help?",
            )
            return

        history = self._histories[user_id]
        _trim_history(history, now=now)
        messages = [{"role": role, "content": msg} for _, role, msg in history]
        messages.append({"role": "user", "content": text})

        content = await self._run_chat_completion(messages)
        await safe_reply(interaction, content)

        history.append((now, "user", text))
        history.append((now, "assistant", content))
        while len(history) > HISTORY_LEN * 2:
            history.popleft()
        self._persist_history(user_id, "user", text)
        self._persist_history(user_id, "assistant", content)

    @nextcord.slash_command(name="ai", description="AI powered utilities")
    async def ai(self, interaction: nextcord.Interaction) -> None:
        """Parent command for subcommands. Not directly invokable."""

    @ai.subcommand(name="chat", description="Chat with the bot (powered by OpenAI).")
    async def ai_chat(
        self,
        interaction: nextcord.Interaction,
        message: str = SlashOption(
            name="message", description="Your message to the bot", required=True
        ),
    ) -> None:
        await self._handle_chat(interaction, message)

    @ai.subcommand(name="chat_reset", description="Clear your AI chat history.")
    async def ai_chat_reset(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        self._histories.pop(interaction.user.id, None)
        history_file = self._history_dir / f"{interaction.user.id}.json"
        try:
            history_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "Failed to remove chat history for %s: %s", interaction.user.id, exc
            )
        await safe_reply(interaction, "Chat history cleared.", ephemeral=True)

    @ai.subcommand(name="chat_summary", description="Summarize recent AI conversations.")
    async def ai_chat_summary(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(with_message=True, ephemeral=True)
        user_id = interaction.user.id
        history = self._load_history(user_id)
        if not history:
            await safe_reply(interaction, "No chat history found.", ephemeral=True)
            return

        conversation = "\n".join(
            f"{h['role']}: {h['content']}" for h in history[-20:]
        )
        client = _ensure_openai_client()
        if not client:
            await safe_reply(
                interaction,
                "Sorry, chat functionality is not available right now.",
                ephemeral=True,
            )
            return

        try:
            summary = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "Summarize the following conversation."},
                        {"role": "user", "content": conversation},
                    ],
                )
            )
            content = summary.choices[0].message.content
        except Exception:
            logger.error("OpenAI error while summarizing.", exc_info=True)
            content = "Failed to generate summary."

        if len(content) > MAX_RESPONSE_LENGTH:
            content = content[: MAX_RESPONSE_LENGTH - 3] + '...'

        await safe_reply(interaction, content, ephemeral=True)

    # ------------------------------------------------------------------
    # Image generation helpers
    # ------------------------------------------------------------------
    @ai.subcommand(name="image", description="Generate an image using DALL-E 3.")
    async def ai_image(self, interaction: nextcord.Interaction, prompt: str) -> None:
        await interaction.response.defer(with_message=True)

        if (
            Config.GUILD_ID
            and interaction.guild
            and interaction.guild.id != Config.GUILD_ID
        ):
            await safe_reply(
                interaction,
                "This command is not available in this server.",
                ephemeral=True,
            )
            return

        client = _ensure_openai_client()
        if not client:
            await safe_reply(
                interaction,
                "Image generation is not available right now.",
                ephemeral=True,
            )
            return

        try:
            response = await asyncio.to_thread(
                lambda: client.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    size="1024x1024",
                )
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "content_policy_violation" in msg:
                await safe_reply(
                    interaction,
                    "The prompt you used violates the content policy. Please try a different prompt.",
                    ephemeral=True,
                )
                return
            await safe_reply(
                interaction,
                f"An error occurred: {exc}",
                ephemeral=True,
            )
            return

        image_url = response.data[0].url
        embed = nextcord.Embed(title="Here's your generated image:")
        embed.set_image(url=image_url)
        await safe_reply(interaction, embed=embed)

    # ------------------------------------------------------------------
    # Voice helpers (placeholder)
    # ------------------------------------------------------------------
    def _voice_chat_enabled(self, guild_id: int | None) -> bool:
        if guild_id is None:
            return True
        return guild_id not in self._disabled_voice_guilds

    @ai.subcommand(name="voice", description="Talk to the bot via voice (preview).")
    async def ai_voice(self, interaction: nextcord.Interaction) -> None:
        if not self._voice_chat_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "Voice chat is currently disabled on this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(with_message=True)

        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.followup.send(
                "You need to be connected to a voice channel to use this command.",
                delete_after=10,
            )
            return

        channel = voice_state.channel
        logger.debug("Connecting to voice channel %s", channel)

        voice_client = interaction.guild.voice_client if interaction.guild else None
        vc = voice_client
        joined_here = False
        try:
            if voice_client:
                if voice_client.channel != channel:
                    await voice_client.move_to(channel)
                vc = voice_client
            else:
                vc = await channel.connect()
                joined_here = True
        except nextcord.ClientException:
            await interaction.followup.send(
                "I'm already connected to a voice channel; disconnect me first.",
                delete_after=10,
            )
            return

        logger.info("Connected to voice channel %s for realtime chat", channel)

        try:
            await interaction.followup.send(
                "Voice chat is not yet fully implemented. This is a placeholder.",
                delete_after=10,
            )
        finally:
            logger.info("Disconnecting from voice channel %s", channel)
            if joined_here and vc:
                await vc.disconnect()

    @ai.subcommand(
        name="voice_toggle",
        description="Enable or disable AI voice chat on this server.",
    )
    async def ai_voice_toggle(
        self, interaction: nextcord.Interaction, enabled: bool
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms and perms.manage_guild):
            await interaction.response.send_message(
                "You need the Manage Server permission to use this command.",
                ephemeral=True,
            )
            return

        if enabled:
            self._disabled_voice_guilds.discard(interaction.guild_id)
            status_message = "Voice chat has been enabled for this server."
        else:
            self._disabled_voice_guilds.add(interaction.guild_id)
            status_message = "Voice chat has been disabled for this server."

        logger.info(
            "Voice chat %s by %s in guild %s",
            "enabled" if enabled else "disabled",
            interaction.user,
            interaction.guild_id,
        )

        await interaction.response.send_message(status_message, ephemeral=True)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(AICog(bot))
    logger.info("Loaded AICog")

