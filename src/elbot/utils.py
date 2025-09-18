# elbot/utils.py

from __future__ import annotations

from typing import Any

from importlib import resources

import nextcord
from nextcord.ext import commands


def load_all_cogs(bot: commands.Bot, package: str = "elbot.cogs") -> None:
    """Dynamically load every ``.py`` file in the given cog package."""

    try:
        package_files = resources.files(package)
    except AttributeError as exc:  # pragma: no cover - Python <3.11 fallback
        raise RuntimeError(f"Cog package '{package}' could not be resolved") from exc

    for entry in package_files.iterdir():
        if entry.suffix != ".py" or entry.name.startswith("_"):
            continue
        module_name = entry.stem
        extension = f"{package}.{module_name}"
        try:
            bot.load_extension(extension)
            print(f'[bot] Loaded cog: {extension}')
        except commands.ExtensionAlreadyLoaded:
            print(f'[bot] Cog already loaded: {extension}')
        except Exception as exc:  # pragma: no cover - defensive logging
            print(f'[bot] Failed to load {extension}: {exc}')

async def safe_reply(
    interaction: nextcord.Interaction, *args: Any, **kwargs: Any
) -> nextcord.Message:
    """Send a response without risking double acknowledgements."""

    responder = getattr(interaction, "response", None)
    followup = getattr(interaction, "followup", None)

    is_done_callable = getattr(responder, "is_done", None)
    is_done = False
    if callable(is_done_callable):
        try:
            is_done = bool(is_done_callable())
        except TypeError:  # pragma: no cover - exotic mocks
            is_done = bool(is_done_callable)
    elif is_done_callable is not None:
        is_done = bool(is_done_callable)

    if (is_done or is_done_callable is None) and followup:
        return await followup.send(*args, **kwargs)

    if responder and hasattr(responder, "send_message") and not is_done:
        return await responder.send_message(*args, **kwargs)

    if followup:
        return await followup.send(*args, **kwargs)

    raise RuntimeError("Interaction cannot send a response")


