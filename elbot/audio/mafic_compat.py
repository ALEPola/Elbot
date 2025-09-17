"""Compatibility helper for importing :mod:`mafic` gracefully.

This project prefers to use the real Mafic dependency for all music
functionality.  However, the test environment used by these kata style
exercises deliberately omits the optional dependency which meant importing
``mafic`` raised :class:`ModuleNotFoundError` during module import.  The
result was that simply importing the music cog crashed before the tests had a
chance to patch in their own doubles.

To make the bot more robust we attempt to import ``mafic`` and fall back to a
light-weight stub when it is unavailable.  The stub exposes the attributes the
rest of the code expects (``Player``, ``NodePool`` and friends) but raises a
clear runtime error if they are actually used.  The tests monkeypatch these
attributes with their own fakes so they can run without the real dependency
installed, while production deployments will continue to use the genuine
library.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import logging


logger = logging.getLogger("elbot.mafic")


def _make_unavailable_class(name: str, message: str):
    """Return a placeholder class that raises ``RuntimeError`` when used."""

    def _init(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover -
        raise RuntimeError(message)

    return type(name, (), {"__init__": _init})


class _MaficStub(SimpleNamespace):
    """Module-like stub used when Mafic is not installed."""

    available: bool = False

    def __init__(self, message: str):
        super().__init__(
            __missing_message__=message,
            NodePool=_make_unavailable_class("NodePool", message),
            Player=_make_unavailable_class("Player", message),
            Node=_make_unavailable_class("Node", message),
            Track=type("Track", (), {}),
        )

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - defensive
        raise RuntimeError(self.__missing_message__)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return "<MaficStub missing>"


def get_mafic() -> Any:
    """Return the Mafic module or a stub when it is unavailable."""

    try:
        import mafic  # type: ignore
    except ModuleNotFoundError as exc:
        message = (
            "Mafic is required for music playback but is not installed. "
            "Install it with 'pip install mafic>=2.10.1' to enable music."
        )
        logger.warning("Using Mafic stub because the real dependency is missing: %s", exc)
        return _MaficStub(message)

    setattr(mafic, "available", True)
    return mafic


__all__ = ["get_mafic"]

