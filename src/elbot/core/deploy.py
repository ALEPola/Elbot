"""Unified helpers for installing services and scheduling auto updates."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from .. import service_install
from . import auto_update

RunFunc = Callable[[list[str]], object]
EnsureCommandFunc = Callable[[str], bool]

WINDOWS_ACTIONS = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "status": "query",
}

SERVICE_NAME_DEFAULT = "elbot.service"
WINDOWS_SERVICE_NAME_DEFAULT = "Elbot"


class DeployError(RuntimeError):
    """Raised when a deployment action cannot be completed."""


def _normalize_platform(name: str) -> str:
    value = name.lower()
    if value in {"windows", "win", "win32", "nt"}:
        return "windows"
    if value in {"darwin", "mac", "macos"}:
        return "darwin"
    if value in {"systemd", "linux", "linux2"}:
        return "systemd"
    return value


def _detect_platform(platform_override: str | None = None) -> str:
    if platform_override:
        normalized = _normalize_platform(platform_override)
        if normalized in {"windows", "darwin", "systemd"}:
            return normalized

    if os.name == "nt":
        return "windows"

    system = platform.system().lower()
    if system == "darwin":
        return "darwin"

    if shutil.which("systemctl"):
        return "systemd"

    return "unsupported"


def install_service(
    project_root: Path,
    *,
    python_executable: str,
    require_lavalink: bool = False,
    platform_override: str | None = None,
) -> None:
    """Install the Elbot service for the detected platform."""

    project_root = Path(project_root)
    platform_name = _detect_platform(platform_override)

    if platform_name == "windows":
        service_install.install_windows_service(project_root, python_executable=python_executable)
        return

    if platform_name == "darwin":
        service_install.install_launchd_service(
            project_root,
            require_lavalink=require_lavalink,
            python_executable=python_executable,
        )
        return

    if platform_name == "systemd":
        service_install.install_systemd_service(
            project_root,
            require_lavalink=require_lavalink,
            python_executable=python_executable,
        )
        return

    raise DeployError("Service management is not supported on this platform.")


def remove_service(*, platform_override: str | None = None) -> None:
    """Remove the Elbot service for the detected platform."""

    platform_name = _detect_platform(platform_override)

    if platform_name == "windows":
        service_install.uninstall_windows_service()
        return

    if platform_name == "darwin":
        service_install.uninstall_launchd_service()
        return

    if platform_name == "systemd":
        service_install.uninstall_systemd_service()
        return

    raise DeployError("Service management is not supported on this platform.")


def control_service(
    action: str,
    *,
    run: RunFunc | None = None,
    ensure_command: EnsureCommandFunc | None = None,
    is_windows: bool | None = None,
    service_name: str = SERVICE_NAME_DEFAULT,
    windows_service_name: str = WINDOWS_SERVICE_NAME_DEFAULT,
    error_cls: type[Exception] = RuntimeError,
):
    """Run a control action against the Elbot service."""

    runner: RunFunc = run or (lambda cmd: subprocess.run(cmd, check=True))
    ensure: EnsureCommandFunc = ensure_command or (lambda name: shutil.which(name) is not None)

    if is_windows is None:
        is_windows = os.name == "nt"

    if is_windows:
        mapped = WINDOWS_ACTIONS.get(action)
        if mapped is None:
            raise error_cls(f"Unsupported service action on Windows: {action}")
        return runner(["sc", mapped, windows_service_name])

    if not ensure("systemctl"):
        raise error_cls("systemctl not available; manage the process manually.")

    if action == "status":
        return runner(["systemctl", "status", service_name])

    if action not in {"start", "stop", "restart"}:
        raise error_cls(f"Unsupported systemd action: {action}")

    return runner(["systemctl", action, service_name])


def enable_auto_update(
    project_root: Path,
    python_executable: str,
    service_name: str = SERVICE_NAME_DEFAULT,
) -> str:
    """Enable scheduled auto updates using systemd timers or cron."""

    project_root = Path(project_root)
    if auto_update.systemd_supported():
        auto_update.enable_systemd_timer(project_root, python_executable, service_name)
        return "systemd"

    if auto_update.cron_supported():
        auto_update.enable_cron(project_root, python_executable, service_name)
        return "cron"

    raise DeployError("No scheduler available (systemd or cron).")


def disable_auto_update() -> str:
    """Disable scheduled auto updates."""

    if auto_update.systemd_supported():
        auto_update.disable_systemd_timer()
        return "systemd"

    if auto_update.cron_supported():
        auto_update.disable_cron()
        return "cron"

    raise DeployError("No scheduler available to disable.")


def auto_update_status() -> auto_update.AutoUpdateStatus:
    """Return the current auto update status."""

    return auto_update.current_status()


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_python() -> str:
    return sys.executable


def _cli_service_install(args: argparse.Namespace) -> None:
    install_service(
        args.root,
        python_executable=args.python,
        require_lavalink=args.require_lavalink,
        platform_override=args.platform,
    )


def _cli_service_remove(args: argparse.Namespace) -> None:
    remove_service(platform_override=args.platform)


def _cli_service_control(args: argparse.Namespace) -> None:
    is_windows = None
    if args.platform:
        is_windows = _detect_platform(args.platform) == "windows"
    result = control_service(
        args.action,
        is_windows=is_windows,
        service_name=args.service_name,
        windows_service_name=args.windows_service_name,
    )
    if isinstance(result, subprocess.CompletedProcess):
        stdout = (result.stdout or "").strip()
        if stdout:
            print(stdout)


def _cli_auto_enable(args: argparse.Namespace) -> None:
    mode = enable_auto_update(args.root, args.python, args.service_name)
    print(f"Auto updates enabled via {mode}.")


def _cli_auto_disable(_: argparse.Namespace) -> None:
    mode = disable_auto_update()
    print(f"Auto updates disabled for {mode}.")


def _cli_auto_status(_: argparse.Namespace) -> None:
    status = auto_update_status()
    print(f"Auto updates: {status.mode}")
    if status.details:
        print(f"  enabled: {status.details.enabled}")
        print(f"  active: {status.details.active}")
        if status.details.last_trigger:
            print(f"  last run: {status.details.last_trigger}")
        if status.details.next_run:
            print(f"  next run: {status.details.next_run}")
        if status.details.error:
            print(f"  error: {status.details.error}")
    else:
        print(f"  cron enabled: {status.cron_enabled}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m elbot.core.deploy")
    sub = parser.add_subparsers(dest="command", required=True)

    service = sub.add_parser("service", help="Manage the Elbot service")
    service_sub = service.add_subparsers(dest="service_command", required=True)

    svc_install = service_sub.add_parser("install", help="Install the service")
    svc_install.add_argument("--root", type=Path, default=_default_project_root())
    svc_install.add_argument("--python", default=_default_python())
    svc_install.add_argument("--require-lavalink", action="store_true")
    svc_install.add_argument("--platform")
    svc_install.set_defaults(func=_cli_service_install)

    svc_remove = service_sub.add_parser("remove", help="Remove the service")
    svc_remove.add_argument("--platform")
    svc_remove.set_defaults(func=_cli_service_remove)

    for action in ("start", "stop", "restart", "status"):
        sub_parser = service_sub.add_parser(action, help=f"{action.title()} the service")
        sub_parser.add_argument("--service-name", default=SERVICE_NAME_DEFAULT)
        sub_parser.add_argument("--windows-service-name", default=WINDOWS_SERVICE_NAME_DEFAULT)
        sub_parser.add_argument("--platform")
        sub_parser.set_defaults(func=_cli_service_control, action=action)

    auto = sub.add_parser("auto-update", help="Manage scheduled auto updates")
    auto_sub = auto.add_subparsers(dest="auto_command", required=True)

    auto_enable = auto_sub.add_parser("enable", help="Enable auto updates")
    auto_enable.add_argument("--root", type=Path, default=_default_project_root())
    auto_enable.add_argument("--python", default=_default_python())
    auto_enable.add_argument("--service-name", default=SERVICE_NAME_DEFAULT)
    auto_enable.set_defaults(func=_cli_auto_enable)

    auto_disable = auto_sub.add_parser("disable", help="Disable auto updates")
    auto_disable.set_defaults(func=_cli_auto_disable)

    auto_status = auto_sub.add_parser("status", help="Show scheduler status")
    auto_status.set_defaults(func=_cli_auto_status)

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        args.func(args)
    except DeployError as exc:
        parser.exit(1, f"error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(exc.returncode or 1, f"Command failed: {' '.join(map(str, exc.cmd))}\n")


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()

