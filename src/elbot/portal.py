"""Compatibility shim for the optional Elbot web portal."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Any

_MODULE: ModuleType | None = None
_IMPORT_ERROR: ModuleNotFoundError | None = None

try:
    if "elbot_portal.app" in sys.modules:
        _MODULE = importlib.reload(sys.modules["elbot_portal.app"])
    else:
        _MODULE = importlib.import_module("elbot_portal.app")
except ModuleNotFoundError as exc:
    _IMPORT_ERROR = exc
else:  # pragma: no cover - exercised through portal tests
    for _name in dir(_MODULE):
        if _name.startswith("__") and _name not in {"__all__", "__doc__"}:
            continue
        globals()[_name] = getattr(_MODULE, _name)
    __doc__ = getattr(_MODULE, "__doc__")
    __all__ = getattr(_MODULE, "__all__", [name for name in dir(_MODULE) if not name.startswith("__")])
    sys.modules[__name__] = _MODULE

if _MODULE is None:
    __all__ = ["main"]

    def __getattr__(name: str) -> Any:
        raise ModuleNotFoundError(
            "The Elbot management portal is optional. Install it with `pip install elbot[portal]`."
        ) from _IMPORT_ERROR

    def main() -> int:
        message = (
            "The Elbot management portal is now optional.\n"
            "Install it with `pip install elbot[portal]` and try again."
        )
        print(message, file=sys.stderr)
        return 1
