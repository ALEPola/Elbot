"""Service management helpers for Elbot CLI refactor."""

from __future__ import annotations

from typing import Callable

RunFunc = Callable[[list[str]], object]
EnsureCommandFunc = Callable[[str], bool]

WINDOWS_ACTIONS = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "status": "query",
}


def install_service(
    run_in_venv: RunFunc,
    *,
    require_lavalink: bool = False,
    force: bool = False,
) -> None:
    args: list[str] = ["-m", "elbot.service_install"]
    if require_lavalink:
        args.append("--require-lavalink")
    if force:
        args.append("--force")
    run_in_venv(args)


def remove_service(run_in_venv: RunFunc) -> None:
    run_in_venv(["-m", "elbot.service_install", "--remove"])


def control_service(
    action: str,
    *,
    is_windows: bool,
    run: RunFunc,
    ensure_command: EnsureCommandFunc,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    if is_windows:
        mapped = WINDOWS_ACTIONS.get(action)
        if mapped is None:
            raise error_cls(f"Unsupported service action on Windows: {action}")
        run(["sc", mapped, "Elbot"])
        return

    if not ensure_command("systemctl"):
        raise error_cls("systemctl not available; manage the process manually.")

    if action == "status":
        run(["systemctl", "status", "elbot.service"])
        return

    if action not in {"start", "stop", "restart"}:
        raise error_cls(f"Unsupported systemd action: {action}")

    run(["systemctl", action, "elbot.service"])
