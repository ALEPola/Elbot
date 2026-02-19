# -*- coding: utf-8 -*-
"""Command-line utility for installing, configuring and managing Elbot."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

from collections.abc import Mapping

from .core import ops

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INFRA_DIR = PROJECT_ROOT / "infra"
DOCKER_DIR = INFRA_DIR / "docker"
SCRIPTS_DIR = INFRA_DIR / "scripts"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
VENV_DIR = PROJECT_ROOT / ".venv"

IS_WINDOWS = os.name == "nt"


REQUIRED_ENV_VARS: dict[str, str] = {
    "DISCORD_TOKEN": "Discord bot token",
}

ENV_OVERRIDE_SOURCES: dict[str, str] = {
    "ELBOT_DISCORD_TOKEN": "DISCORD_TOKEN",
    "ELBOT_OPENAI_KEY": "OPENAI_API_KEY",
    "ELBOT_LAVALINK_PASSWORD": "LAVALINK_PASSWORD",
    "ELBOT_LAVALINK_HOST": "LAVALINK_HOST",
    "ELBOT_USERNAME": "ELBOT_USERNAME",
    "ELBOT_AUTO_UPDATE_WEBHOOK": "AUTO_UPDATE_WEBHOOK",
}

OPTIONAL_ENV_VARS: dict[str, str] = {
    "OPENAI_API_KEY": "OpenAI API key (optional)",
    "LAVALINK_PASSWORD": "Lavalink password (leave blank to keep default)",
    "LAVALINK_HOST": "Lavalink host (optional)",
    "ELBOT_USERNAME": "Bot username (press Enter to keep default)",
    "AUTO_UPDATE_WEBHOOK": "Webhook URL for auto-update failure alerts (optional)",
}


DEFAULT_PORT_HINTS: dict[str, int] = {
    "LAVALINK_PORT": 2333,
    "PORT": 8000,
}


def _parse_port(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if port > 0 else None


def _build_port_conflict_state(env_pairs: Mapping[str, str]) -> tuple[dict[int, tuple[str, ...]], tuple[int, ...]]:
    hints: dict[int, list[str]] = {}
    for env_key, default_port in DEFAULT_PORT_HINTS.items():
        configured = _parse_port(env_pairs.get(env_key))
        port = configured if configured is not None else default_port
        if port:
            hints.setdefault(port, []).append(env_key)

    normalized = {port: tuple(names) for port, names in hints.items()}
    return normalized, tuple(normalized.keys())


def _current_port_conflict_state() -> tuple[dict[int, tuple[str, ...]], tuple[int, ...]]:
    hints, ports = _build_port_conflict_state(ops.read_env(ENV_FILE))
    global PORT_CONFLICT_HINTS, PORT_CONFLICT_PORTS
    PORT_CONFLICT_HINTS = hints
    PORT_CONFLICT_PORTS = ports
    return hints, ports


PORT_CONFLICT_HINTS: dict[int, tuple[str, ...]]
PORT_CONFLICT_PORTS: tuple[int, ...]
PORT_CONFLICT_HINTS, PORT_CONFLICT_PORTS = _build_port_conflict_state(ops.read_env(ENV_FILE))


class CommandError(RuntimeError):
    pass


def _echo(message: str) -> None:
    print(message)


def _run(cmd: list[str], *, cwd: Optional[Path] = None, check: bool = True, env: Optional[dict[str, str]] = None) -> subprocess.CompletedProcess:
    display = " ".join(cmd)
    _echo(f"[elbotctl] $ {display}")
    return subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, check=check, env=env)


def _ensure_command(name: str) -> bool:
    return shutil.which(name) is not None


def _warn_port_conflicts() -> None:
    hints, ports = _current_port_conflict_state()
    conflicts = ops.detect_port_conflicts(ports)
    if not conflicts:
        return

    ports = ", ".join(str(port) for port in conflicts)
    _echo(f"Warning: the following ports appear to be in use: {ports}.")

    hint_names: list[str] = []
    for port in conflicts:
        hint_names.extend(hints.get(port, ()))

    seen: set[str] = set()
    deduped: list[str] = []
    for name in hint_names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)

    if deduped:
        if len(deduped) == 1:
            _echo(f"Stop other services or adjust {deduped[0]} in .env before continuing.")
        elif len(deduped) == 2:
            joined = " and ".join(deduped)
            _echo(f"Stop other services or adjust {joined} in .env before continuing.")
        else:
            joined = ", ".join(deduped[:-1]) + f", and {deduped[-1]}"
            _echo(f"Stop other services or adjust {joined} in .env before continuing.")
    else:
        _echo("Stop other services or update your configuration to avoid the conflict.")


def _run_in_venv(args: Iterable[str]) -> subprocess.CompletedProcess:
    return ops.run_in_venv(
        args,
        venv_dir=VENV_DIR,
        is_windows=IS_WINDOWS,
        run=_run,
        error_cls=CommandError,
    )


def _pip_install(args: Iterable[str]) -> None:
    ops.pip_install(
        args,
        venv_dir=VENV_DIR,
        is_windows=IS_WINDOWS,
        run=_run,
        error_cls=CommandError,
    )




def command_install(args: argparse.Namespace) -> None:
    overrides: dict[str, str] = {}
    if args.env_file:
        overrides.update(ops.read_env(args.env_file))

    env_overrides = {
        target: os.environ[source]
        for source, target in ENV_OVERRIDE_SOURCES.items()
        if os.environ.get(source)
    }
    overrides.update(env_overrides)

    _warn_port_conflicts()
    ops.ensure_prerequisites(
        install_packages=args.install_system_packages,
        non_interactive=args.non_interactive,
        platform_name=platform.system(),
        ensure_command=_ensure_command,
        which=shutil.which,
        run=_run,
        echo=_echo,
    )
    ops.create_venv(
        VENV_DIR,
        force=args.recreate,
        run=_run,
        echo=_echo,
    )
    _pip_install(["install", "-U", "pip", "wheel"])
    if (PROJECT_ROOT / "requirements.txt").exists():
        _pip_install(["install", "-r", "requirements.txt"])
    _pip_install(["install", "-e", str(PROJECT_ROOT)])
    _pip_install(["install", "textblob"])  # ensure corpora command available
    _run_in_venv(["-m", "textblob.download_corpora"])
    ops.prompt_env(
        ENV_FILE,
        ENV_EXAMPLE,
        non_interactive=args.non_interactive,
        overrides=overrides,
        required=REQUIRED_ENV_VARS,
        optional=OPTIONAL_ENV_VARS,
        error_cls=CommandError,
    )

    if not args.no_service:
        try:
            ops.install_service(
                _run_in_venv,
                require_lavalink=args.require_lavalink,
            )
        except subprocess.CalledProcessError as exc:
            _echo("Service installation failed (likely missing permissions).")
            if IS_WINDOWS:
                _echo("Run `elbotctl service install` from an elevated PowerShell prompt to register the service.")
            else:
                _echo("Retry with sudo: `sudo elbotctl service install --require-lavalink` or rerun the installer with --no-service.")
            raise
    _echo("Installation complete. Use 'elbotctl service start' or 'elbotctl run' to launch the bot.")


def command_env_set(args: argparse.Namespace) -> None:
    ops.ensure_env_file(ENV_FILE, ENV_EXAMPLE)
    ops.update_env_var(ENV_FILE, args.key, args.value)
    _echo(f"Set {args.key}")


def command_env_get(args: argparse.Namespace) -> None:
    env = ops.read_env(ENV_FILE)
    if args.key in env:
        _echo(env[args.key])
    else:
        raise CommandError(f"{args.key} not found in {ENV_FILE}")


def command_env_list(_: argparse.Namespace) -> None:
    env = ops.read_env(ENV_FILE)
    for key in sorted(env):
        _echo(f"{key}={env[key]}")


def command_env_import(args: argparse.Namespace) -> None:
    values = ops.read_env(args.file)
    if not values:
        raise CommandError("No key=value pairs found in provided file")
    ops.ensure_env_file(ENV_FILE, ENV_EXAMPLE)
    for key, value in values.items():
        ops.update_env_var(ENV_FILE, key, value)
    _echo(f"Imported {len(values)} values into {ENV_FILE}")



def command_service_install(args: argparse.Namespace) -> None:
    ops.install_service(
        _run_in_venv,
        require_lavalink=args.require_lavalink,
        force=args.force,
    )


def command_service_remove(_: argparse.Namespace) -> None:
    ops.remove_service(_run_in_venv)


def command_service_start(_: argparse.Namespace) -> None:
    ops.control_service(
        "start",
        is_windows=IS_WINDOWS,
        run=_run,
        ensure_command=_ensure_command,
        error_cls=CommandError,
    )


def command_service_stop(_: argparse.Namespace) -> None:
    ops.control_service(
        "stop",
        is_windows=IS_WINDOWS,
        run=_run,
        ensure_command=_ensure_command,
        error_cls=CommandError,
    )


def command_service_restart(_: argparse.Namespace) -> None:
    ops.control_service(
        "restart",
        is_windows=IS_WINDOWS,
        run=_run,
        ensure_command=_ensure_command,
        error_cls=CommandError,
    )


def command_service_status(_: argparse.Namespace) -> None:
    ops.control_service(
        "status",
        is_windows=IS_WINDOWS,
        run=_run,
        ensure_command=_ensure_command,
        error_cls=CommandError,
    )


def command_update(args: argparse.Namespace) -> None:
    if args.check:
        _echo("Update check complete. No changes were applied.")
        return

    if (PROJECT_ROOT / ".git").exists() and not args.skip_pull and _ensure_command("git"):
        _run(["git", "pull", "--ff-only"])
    if not args.skip_deps:
        _pip_install(["install", "-U", "pip", "wheel"])
        if (PROJECT_ROOT / "requirements.txt").exists():
            _pip_install(["install", "-r", "requirements.txt"])
        _pip_install(["install", "-e", str(PROJECT_ROOT)])
    if not args.skip_service:
        try:
            ops.control_service(
                "restart",
                is_windows=IS_WINDOWS,
                run=_run,
                ensure_command=_ensure_command,
                error_cls=CommandError,
            )
        except CommandError:
            _echo("Service restart skipped (service not installed or unsupported).")


def command_run(_: argparse.Namespace) -> None:
    _run_in_venv(["-m", "elbot.main"])


def command_check(_: argparse.Namespace) -> None:
    from .config import Config, log_cookie_status  # type: ignore import lazily

    Config.validate()
    log_cookie_status()

    try:
        from .main import _lavalink_health_check  # type: ignore
        import asyncio

        healthy, failure_reason = asyncio.run(_lavalink_health_check())
    except Exception as exc:  # pragma: no cover - optional diagnostics
        raise CommandError(f"Lavalink health check failed: {exc}") from exc

    if not healthy:
        detail = f": {failure_reason}" if failure_reason else ""
        raise CommandError(f"Lavalink health check failed{detail}")

    _echo("Configuration looks good.")


def command_doctor(_: argparse.Namespace) -> None:
    _echo("=== Elbot Health Check ===")
    _echo("")
    _echo("Checking env...")
    env_data = ops.read_env(ENV_FILE)
    if env_data.get("DISCORD_TOKEN"):
        _echo("✅ Discord token detected")
    else:
        _echo("❌ Discord token missing - check .env file")

    _echo("")
    _echo("Checking dependencies")
    for cmd in ["python", "java", "ffmpeg"]:
        if _ensure_command(cmd):
            _echo(f"✅ {cmd} found")
        else:
            _echo(f"❌ {cmd} missing - check your PATH")

    _echo("")
    _echo("Checking setup")
    if (PROJECT_ROOT / "DISCORD_SETUP.md").exists():
        _echo("✅ Setup docs found")
    else:
        _echo("❌ If you see ❌, check DISCORD_SETUP.md for help")


def command_logs(args: argparse.Namespace) -> None:
    lines = args.lines or 100
    if IS_WINDOWS:
        _echo("Viewing Windows service logs is not automated yet. Use 'Get-WinEvent -LogName Application' and filter for Elbot.")
        return
    if not _ensure_command("journalctl"):
        raise CommandError("journalctl not available")
    cmd = ["journalctl", "-u", "elbot.service", "-n", str(lines)]
    if args.follow:
        cmd.append("-f")
    _run(cmd, check=False)


def command_docker(args: argparse.Namespace) -> None:
    ops.run_compose_action(
        args.action,
        docker_dir=DOCKER_DIR,
        run=_run,
        remove_orphans=getattr(args, "remove_orphans", False),
        follow=getattr(args, "follow", False),
        error_cls=CommandError,
    )


def command_uninstall(args: argparse.Namespace) -> None:
    if IS_WINDOWS:
        _echo("Removing Windows service (if installed)...")
        _run_in_venv(["-m", "elbot.service_install", "--remove"])
        if args.delete:
            _echo("Delete the project directory manually once this process exits (file handles prevent auto-removal on Windows).")
        if args.purge:
            _echo("Package purge is not supported on Windows.")
        return
    script = SCRIPTS_DIR / "uninstall.sh"
    if not script.exists():
        raise CommandError("uninstall script not found")
    cmd = ["bash", str(script)]
    if args.delete:
        cmd.append("--delete")
    if args.purge:
        cmd.append("--purge")
    _run(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elbotctl", description="Elbot management utility")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="Bootstrap the project (venv, deps, .env, optional service)")
    install.add_argument("--non-interactive", "--yes", action="store_true", dest="non_interactive", help="Fail instead of prompting for missing secrets (same as --yes)")
    install.add_argument("--env-file", type=Path, help="Import key=value pairs from a file before prompting")
    install.add_argument("--no-service", action="store_true", help="Skip installing the background service")
    install.add_argument("--require-lavalink", action="store_true", help="Require Lavalink service during install")
    install.add_argument("--install-system-packages", action="store_true", help="Attempt to install ffmpeg/java/git via apt-get when missing")
    install.add_argument("--recreate", action="store_true", help="Recreate the virtual environment even if it exists")
    install.set_defaults(func=command_install)

    env_parser = sub.add_parser("env", help="Inspect or modify the .env file")
    env_sub = env_parser.add_subparsers(dest="env_command", required=True)
    env_set = env_sub.add_parser("set", help="Set a variable")
    env_set.add_argument("key")
    env_set.add_argument("value")
    env_set.set_defaults(func=command_env_set)
    env_get = env_sub.add_parser("get", help="Read a variable")
    env_get.add_argument("key")
    env_get.set_defaults(func=command_env_get)
    env_list = env_sub.add_parser("list", help="Show all variables")
    env_list.set_defaults(func=command_env_list)
    env_import = env_sub.add_parser("import", help="Load values from another env file")
    env_import.add_argument("file", type=Path)
    env_import.set_defaults(func=command_env_import)

    service = sub.add_parser("service", help="Manage the system service")
    service_sub = service.add_subparsers(dest="service_command", required=True)
    svc_install = service_sub.add_parser("install", help="Install the service")
    svc_install.add_argument("--require-lavalink", action="store_true")
    svc_install.add_argument("--force", action="store_true", help="Force reinstall if already present")
    svc_install.set_defaults(func=command_service_install)
    service_sub.add_parser("remove", help="Remove the service").set_defaults(func=command_service_remove)
    service_sub.add_parser("start", help="Start the service").set_defaults(func=command_service_start)
    service_sub.add_parser("stop", help="Stop the service").set_defaults(func=command_service_stop)
    service_sub.add_parser("restart", help="Restart the service").set_defaults(func=command_service_restart)
    service_sub.add_parser("status", help="Show service status").set_defaults(func=command_service_status)

    updater = sub.add_parser("update", help="Pull latest code and refresh dependencies")
    updater.add_argument("--check", action="store_true", help="Report update availability without applying changes")
    updater.add_argument("--skip-pull", action="store_true")
    updater.add_argument("--skip-deps", action="store_true")
    updater.add_argument("--skip-service", action="store_true")
    updater.set_defaults(func=command_update)

    runner = sub.add_parser("run", help="Run the bot in the foreground")
    runner.set_defaults(func=command_run)

    doctor = sub.add_parser("doctor", help="Check if everything is working")
    doctor.set_defaults(func=command_doctor)

    checker = sub.add_parser("check", help="Validate configuration and Lavalink connectivity")
    checker.set_defaults(func=command_check)

    logs = sub.add_parser("logs", help="Tail service logs (systemd only)")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--follow", action="store_true")
    logs.set_defaults(func=command_logs)

    docker = sub.add_parser("docker", help="Manage the docker-compose stack")
    docker_sub = docker.add_subparsers(dest="action", required=True)
    docker_up = docker_sub.add_parser("up", help="Build and start containers")
    docker_up.add_argument("--remove-orphans", action="store_true")
    docker_up.set_defaults(func=command_docker)
    docker_down = docker_sub.add_parser("down", help="Stop containers")
    docker_down.set_defaults(func=command_docker)
    docker_pull = docker_sub.add_parser("pull", help="Pull latest images")
    docker_pull.set_defaults(func=command_docker)
    docker_logs = docker_sub.add_parser("logs", help="Tail container logs")
    docker_logs.add_argument("--follow", action="store_true")
    docker_logs.set_defaults(func=command_docker)

    uninstall = sub.add_parser("uninstall", help="Remove services and optionally delete sources")
    uninstall.add_argument("--delete", action="store_true", help="Delete the project directory")
    uninstall.add_argument("--purge", action="store_true", help="Remove system packages such as Java/ffmpeg (apt-get)")
    uninstall.set_defaults(func=command_uninstall)

    return parser


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        args.func(args)
    except CommandError as exc:
        parser.exit(1, f"error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(exc.returncode or 1, f"Command failed: {' '.join(map(str, exc.cmd))}\n")


if __name__ == "__main__":
    main()
