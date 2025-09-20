# elbot/main.py

from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import aiohttp
import nextcord
os.environ.setdefault("MAFIC_IGNORE_LIBRARY_CHECK", "1")
import mafic
from nextcord.ext import commands

from .config import Config, log_cookie_status
from .utils import load_all_cogs, safe_reply


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("elbot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    log_path = Path(Config.BASE_DIR) / "logs" / "elbot.log"
    log_path.parent.mkdir(exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")

    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logging.getLogger("nextcord").setLevel(logging.INFO)
    logging.getLogger("mafic").setLevel(logging.INFO)
    return logger


logger = _setup_logging()


async def _fetch_lavalink_plugins(response_json: Any) -> str:
    if not isinstance(response_json, dict):
        return "unknown"

    plugins = response_json.get("plugins") or response_json.get("pluginInfo")
    if isinstance(plugins, dict):
        plugins = [plugins]
    if not isinstance(plugins, list):
        return "unknown"

    for plugin in plugins:
        if not isinstance(plugin, dict):
            continue
        dependency = plugin.get("dependency") or plugin.get("artifactId")
        version = plugin.get("version")
        name = plugin.get("name")
        if dependency and "youtube" in dependency:
            if version:
                return version
            parts = dependency.split(":")
            if len(parts) >= 3:
                return parts[-1]
        if name and "youtube" in name.lower():
            return version or "unknown"
    return "unknown"


async def _lavalink_health_check() -> tuple[bool, Optional[str]]:
    host = Config.LAVALINK_HOST
    port = Config.LAVALINK_PORT
    password = Config.LAVALINK_PASSWORD
    secure = os.getenv("LAVALINK_SSL", "false").lower() == "true"
    scheme = "https" if secure else "http"
    base_url = f"{scheme}://{host}:{port}"

    timeout = aiohttp.ClientTimeout(total=5)
    handshake = False
    yt_version = "unknown"
    failure_reason: Optional[str] = None

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{base_url}/version", headers={"Authorization": password}
            ) as response:
                if response.status == 200:
                    handshake = True
                    try:
                        data = await response.json(content_type=None)
                    except Exception:  # pragma: no cover - Lavalink version quirks
                        data = None
                    yt_version = await _fetch_lavalink_plugins(data)
                else:
                    failure_reason = f"/version status={response.status}"
            if handshake:
                params = {"identifier": "ytsearch:elbot health"}
                async with session.get(
                    f"{base_url}/loadtracks",
                    headers={"Authorization": password},
                    params=params,
                ) as track_response:
                    if track_response.status != 200:
                        handshake = False
                        failure_reason = f"/loadtracks status={track_response.status}"
                    else:
                        try:
                            load_data = await track_response.json(content_type=None)
                        except Exception as exc:
                            handshake = False
                            failure_reason = f"/loadtracks parse error: {exc}"
                        else:
                            tracks: list[Any] = []
                            if isinstance(load_data, dict):
                                possible_tracks = load_data.get("tracks") or load_data.get("data")
                                if isinstance(possible_tracks, list):
                                    tracks = possible_tracks
                            if not tracks:
                                handshake = False
                                failure_reason = "/loadtracks returned no tracks"
    except Exception as exc:  # pragma: no cover - network failures
        failure_reason = str(exc)

    status = "ok" if handshake else "failed"
    if failure_reason:
        status = f"{status} ({failure_reason})"

    logger.info(
        "lavalink health host=%s port=%s youtube_plugin=%s handshake=%s",
        host,
        port,
        yt_version,
        status,
    )

    return handshake, failure_reason


def main() -> None:
    if Config.AUTO_LAVALINK:
        try:
            from elbot.auto_lavalink import start as start_lavalink

            port, pw = start_lavalink()
            logger.info("auto-lavalink started host=127.0.0.1 port=%s", port)
        except Exception as exc:  # pragma: no cover - startup helper fallback
            logger.error("auto-lavalink failed: %s", exc)

    Config.validate()
    log_cookie_status()
    asyncio.run(_lavalink_health_check())

    intents = nextcord.Intents.default()
    intents.message_content = True
    intents.voice_states = True

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    bot = commands.Bot(
        command_prefix=Config.PREFIX,
        intents=intents,
        description=f"{Config.BOT_USERNAME} Discord bot",
    )

    @bot.event
    async def on_command_error(ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(f"❌ Command not found. Use `{Config.PREFIX}help`.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Missing required argument.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ Command on cooldown. Try in {round(error.retry_after, 2)}s."
            )
        else:
            logger.exception("command error")
            await ctx.send("❌ An unexpected error occurred. Contact the admin.")

    @bot.listen()
    async def on_application_command_error(
        interaction: nextcord.Interaction, error: Exception
    ) -> None:
        logger.exception(
            "slash command error", extra={"command": getattr(interaction, "data", {})}
        )
        await safe_reply(
            interaction,
            "⚠️ Something went wrong while running that command. The team has been notified.",
            ephemeral=True,
        )

    @bot.event
    async def on_ready() -> None:
        if bot.user is None:  # pragma: no cover - defensive
            return
        logger.info("bot ready user=%s id=%s", bot.user, bot.user.id)

    load_all_cogs(bot)

    @bot.slash_command(name="musicdebug", description="Show Lavalink status")
    async def musicdebug(inter: nextcord.Interaction) -> None:
        await inter.response.defer(with_message=True, ephemeral=True)
        nodes = mafic.NodePool.label_to_node
        if not nodes:
            status = "No Lavalink nodes are connected."
        else:
            node = next(iter(nodes.values()))
            status = (
                f"{node.label} available={node.available} "
                f"players={len(node.players)}"
            )
        await safe_reply(inter, status, ephemeral=True)

    try:
        bot.run(Config.DISCORD_TOKEN)
    except Exception:  # pragma: no cover - network/auth failures
        logger.exception("bot failed to start")
        raise


if __name__ == "__main__":
    main()
