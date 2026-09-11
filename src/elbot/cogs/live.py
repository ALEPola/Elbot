"""ELBOT Live: talk to GPT-Live by saying the bot's name in voice."""

from pathlib import Path

import nextcord
from nextcord.ext import commands

from elbot.config import Config
from elbot.live.controller import LiveController
from elbot.live.session import LiveConfig, UsageLedger


class Live(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.controllers: dict[int, LiveController] = {}
        self.ledger = UsageLedger(Path(Config.BASE_DIR) / "logs" / "live_usage.json")

    @nextcord.slash_command(name="live", description="Voice conversation with ELBOT (GPT-Live)")
    async def live(self, interaction: nextcord.Interaction):
        pass

    def _config(self) -> LiveConfig:
        return LiveConfig.from_env()

    @live.subcommand(name="start", description="Start ELBOT Live in your voice channel")
    async def start(self, interaction: nextcord.Interaction):
        if not interaction.guild or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Manage Server permission is required.", ephemeral=True)
            return
        if interaction.guild.id in self.controllers:
            await interaction.response.send_message("ELBOT Live is already running here.", ephemeral=True)
            return
        config = self._config()
        if not config.api_key:
            await interaction.response.send_message("OPENAI_API_KEY is not configured.", ephemeral=True)
            return
        music = self.bot.get_cog("Music")
        if music is None:
            await interaction.response.send_message("The music cog is not loaded.", ephemeral=True)
            return
        await interaction.response.defer()
        player, error = await music._ensure_voice(interaction)
        if error or player is None:
            await interaction.followup.send(error or "Could not join voice.", ephemeral=True)
            return
        if not hasattr(player, "start_listening"):
            await interaction.followup.send(
                "ELBOT Live needs the bridge voice transport (ELBOT_VOICE_TRANSPORT=bridge).", ephemeral=True,
            )
            return
        channel = interaction.channel

        async def announce(text: str):
            if channel is not None:
                await channel.send(text)

        async def notify(event, outcome):
            if outcome == "busy":
                await announce(f"One at a time — **{event.speaker.display_name}**, hang on.")

        async def on_stopped(controller, reason):
            self.controllers.pop(interaction.guild.id, None)
            music.voice_holds.discard(interaction.guild.id)
            await announce(
                f"ELBOT Live stopped ({reason}). This run: {controller.stats['sessions']} session(s), "
                f"{controller.stats['seconds'] / 60:.1f} min, ${controller.stats['usd']:.2f}."
            )

        try:
            await player.start_listening(seconds=3600, notify=notify)
        except Exception as exc:
            await interaction.followup.send(f"Could not start listening: {exc}", ephemeral=True)
            return
        if player.wake is None:
            await player.stop_listening()
            await interaction.followup.send("Wake-phrase detection is unavailable on this host.", ephemeral=True)
            return
        controller = LiveController(player, config, self.ledger, announce=announce, on_stopped=on_stopped)
        self.controllers[interaction.guild.id] = controller
        music.voice_holds.add(interaction.guild.id)  # keep the music cog's idle timer from leaving
        await controller.start()
        day_usd, _ = self.ledger.usd(config.price_per_minute)
        await interaction.followup.send(
            f"ELBOT Live is on in {player.channel.mention}. Say **\"ELBOT\"** and then talk to me. "
            "Only speech addressed to me after my name is sent to OpenAI; nothing else leaves this server, "
            "and no audio or transcripts are stored. "
            f"Limits: {config.session_max_s / 60:.0f} min per session, ${config.daily_usd:.2f}/day "
            f"(${day_usd:.2f} used today). Use /live stop to end it."
        )

    @live.subcommand(name="stop", description="Stop ELBOT Live")
    async def stop(self, interaction: nextcord.Interaction):
        controller = self.controllers.get(interaction.guild.id) if interaction.guild else None
        if controller is None:
            await interaction.response.send_message("ELBOT Live is not running.", ephemeral=True)
            return
        member_channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if member_channel != controller.player.channel and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Join ELBOT's voice channel to stop it.", ephemeral=True)
            return
        await interaction.response.defer()
        await controller.stop(f"stopped by {interaction.user.display_name}")
        try:
            await controller.player.stop_listening()
        except Exception:
            pass
        await interaction.followup.send("ELBOT Live is off.")

    @live.subcommand(name="status", description="Show ELBOT Live state")
    async def status(self, interaction: nextcord.Interaction):
        controller = self.controllers.get(interaction.guild.id) if interaction.guild else None
        if controller is None:
            await interaction.response.send_message("ELBOT Live is not running.", ephemeral=True)
            return
        session = controller.session
        connected = f"connected {session.connected_seconds():.0f}s" if session and session.connected else "idle (not connected)"
        speaker = controller.active_name or "nobody"
        await interaction.response.send_message(
            f"Live: on · GPT-Live: {connected} · Talking: {speaker} · "
            f"Wakes: {controller.stats['wakes']} · Sessions: {controller.stats['sessions']} · "
            f"This run: {controller.stats['seconds'] / 60:.1f} min, ${controller.stats['usd']:.2f}",
            ephemeral=True,
        )

    @live.subcommand(name="cost", description="Show GPT-Live spend against the caps")
    async def cost(self, interaction: nextcord.Interaction):
        config = self._config()
        day_usd, month_usd = self.ledger.usd(config.price_per_minute)
        day_s, month_s = self.ledger.seconds()
        await interaction.response.send_message(
            f"Today: {day_s / 60:.1f} min ≈ ${day_usd:.2f} of ${config.daily_usd:.2f} · "
            f"This month: {month_s / 60:.1f} min ≈ ${month_usd:.2f} of ${config.monthly_usd:.2f} · "
            f"Rate ${config.price_per_minute:.2f}/min (voice only; backend tokens billed separately).",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_wake_phrase(self, player, event, outcome):
        controller = self.controllers.get(player.guild.id)
        if controller is not None and controller.player is player:
            await controller.on_wake(event, outcome)


def setup(bot):
    bot.add_cog(Live(bot))
