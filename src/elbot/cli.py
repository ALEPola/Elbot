# -*- coding: utf-8 -*-
"""Command-line utility for installing, configuring and managing Elbot."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from getpass import getpass
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INFRA_DIR = PROJECT_ROOT / "infra"
DOCKER_DIR = INFRA_DIR / "docker"
SCRIPTS_DIR = INFRA_DIR / "scripts"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
VENV_DIR = PROJECT_ROOT / ".venv"

IS_WINDOWS = os.name == "nt"


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


def _venv_python() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _venv_pip() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def _create_venv(force: bool = False) -> None:
    if VENV_DIR.exists() and not force:
        return
    if VENV_DIR.exists() and force:
        shutil.rmtree(VENV_DIR)
    _echo("Creating virtual environment (.venv)...")
    _run([sys.executable, "-m", "venv", str(VENV_DIR)])


def _run_in_venv(args: list[str]) -> subprocess.CompletedProcess:
    python = _venv_python()
    if not python.exists():
        raise CommandError("virtual environment not found; run 'elbotctl install' first")
    return _run([str(python), *args])


def _pip_install(args: list[str]) -> None:
    pip = _venv_pip()
    if not pip.exists():
        raise CommandError("pip not available in the virtual environment")
    _run([str(pip), *args])


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, data: dict[str, str]) -> None:
    lines: list[str] = []
    existing = _read_env(path)
    existing.update(data)
    for key in sorted(existing.keys()):
        lines.append(f"{key}={existing[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_env_var(key: str, value: str) -> None:
    pairs = _read_env(ENV_FILE)
    pairs[key] = value
    _write_env(ENV_FILE, pairs)


def _ensure_env_file() -> None:
    if not ENV_FILE.exists() and ENV_EXAMPLE.exists():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)


def _prompt_env(non_interactive: bool, overrides: dict[str, str] | None = None) -> None:
    _ensure_env_file()
    env_pairs = _read_env(ENV_FILE)

    if overrides:
        for key, value in overrides.items():
            if value is not None:
                env_pairs[key] = value
        _write_env(ENV_FILE, env_pairs)
        env_pairs = _read_env(ENV_FILE)

    required = {"DISCORD_TOKEN": "Discord bot token"}
    optional = {
        "OPENAI_API_KEY": "OpenAI API key (optional)",
        "LAVALINK_PASSWORD": "Lavalink password (leave blank to keep default)",
        "LAVALINK_HOST": "Lavalink host (optional)",
        "ELBOT_USERNAME": "Bot username (press Enter to keep default)",
    }

    for key, prompt in required.items():
        if env_pairs.get(key):
            continue
        if non_interactive:
            raise CommandError(f"Missing required environment variable: {key}")
        value = getpass(f"{prompt}: ").strip()
        if not value:
            raise CommandError(f"{key} may not be empty")
        _update_env_var(key, value)

    if not non_interactive:
        for key, prompt in optional.items():
            current = env_pairs.get(key, "")
            display = current or ("Elbot" if key == "ELBOT_USERNAME" else "skip")
            msg = f"{prompt} [{display}]: "
            value = getpass(msg) if "key" in key.lower() else input(msg)
            value = value.strip()
            if value:
                _update_env_var(key, value)


def _install_prerequisites(install_packages: bool, non_interactive: bool) -> None:
    missing = []
    for cmd in ("ffmpeg", "java", "git"):
        if not _ensure_command(cmd):
            missing.append(cmd)
    if not missing:
        return

    _echo("Missing prerequisites detected: " + ", ".join(missing))
    if install_packages and platform.system().lower() in {"linux", "darwin"}:
        if shutil.which("apt-get"):
            cmd = ["sudo", "apt-get", "install", "-y"] + missing
            if not non_interactive:
                consent = input(f"Install via {' '.join(cmd)}? [Y/n] ").strip().lower() or "y"
                if consent != "y":
                    install_packages = False
            if install_packages:
                _run(cmd)
                return
    _echo("Please install the missing tools manually and re-run the installer.")


def command_install(args: argparse.Namespace) -> None:
    overrides: dict[str, str] = {}
    if args.env_file:
        overrides.update(_read_env(args.env_file))

    _install_prerequisites(args.install_system_packages, args.non_interactive)
    _create_venv(force=args.recreate)
    _pip_install(["install", "-U", "pip", "wheel"])
    if (PROJECT_ROOT / "requirements.txt").exists():
        _pip_install(["install", "-r", "requirements.txt"])
    _pip_install(["install", "-e", str(PROJECT_ROOT)])
    _pip_install(["install", "textblob"])  # ensure corpora command available
    _run_in_venv(["-m", "textblob.download_corpora"])
    _ensure_env_file()
    _prompt_env(args.non_interactive, overrides)

    if not args.no_service:
        install_service_args = ["-m", "elbot.service_install"]
        if args.require_lavalink:
            install_service_args.append("--require-lavalink")
        try:
            _run_in_venv(install_service_args)
        except subprocess.CalledProcessError as exc:
            _echo("Service installation failed (likely missing permissions).")
            if IS_WINDOWS:
                _echo("Run `elbotctl service install` from an elevated PowerShell prompt to register the service.")
            else:
                _echo("Retry with sudo: `sudo elbotctl service install --require-lavalink` or rerun the installer with --no-service.")
            raise
    _echo("Installation complete. Use 'elbotctl service start' or 'elbotctl run' to launch the bot.")


def command_env_set(args: argparse.Namespace) -> None:
    _ensure_env_file()
    _update_env_var(args.key, args.value)
    _echo(f"Set {args.key}")


def command_env_get(args: argparse.Namespace) -> None:
    env = _read_env(ENV_FILE)
    if args.key in env:
        _echo(env[args.key])
    else:
        raise CommandError(f"{args.key} not found in {ENV_FILE}")


def command_env_list(_: argparse.Namespace) -> None:
    env = _read_env(ENV_FILE)
    for key in sorted(env):
        _echo(f"{key}={env[key]}")


def command_env_import(args: argparse.Namespace) -> None:
    values = _read_env(args.file)
    if not values:
        raise CommandError("No key=value pairs found in provided file")
    _ensure_env_file()
    for key, value in values.items():
        _update_env_var(key, value)
    _echo(f"Imported {len(values)} values into {ENV_FILE}")


def _service_command(action: str) -> None:
    if IS_WINDOWS:
        mapping = {"start": "start", "stop": "stop", "restart": "restart", "status": "query"}
        cmd = ["sc", mapping[action], "Elbot"]
        _run(cmd)
        return
    if not _ensure_command("systemctl"):
        raise CommandError("systemctl not available; manage the process manually.")
    if action == "status":
        _run(["systemctl", "status", "elbot.service"])
    else:
        _run(["systemctl", action, "elbot.service"])


def command_service_install(args: argparse.Namespace) -> None:
    install_args = ["-m", "elbot.service_install"]
    if args.require_lavalink:
        install_args.append("--require-lavalink")
    if args.force:
        install_args.append("--force")
    _run_in_venv(install_args)


def command_service_remove(_: argparse.Namespace) -> None:
    _run_in_venv(["-m", "elbot.service_install", "--remove"])


def command_service_start(_: argparse.Namespace) -> None:
    _service_command("start")


def command_service_stop(_: argparse.Namespace) -> None:
    _service_command("stop")


def command_service_restart(_: argparse.Namespace) -> None:
    _service_command("restart")


def command_service_status(_: argparse.Namespace) -> None:
    _service_command("status")


def command_update(args: argparse.Namespace) -> None:
    if (PROJECT_ROOT / ".git").exists() and not args.skip_pull and _ensure_command("git"):
        _run(["git", "pull", "--ff-only"])
    if not args.skip_deps:
        _pip_install(["install", "-U", "pip", "wheel"])
        if (PROJECT_ROOT / "requirements.txt").exists():
            _pip_install(["install", "-r", "requirements.txt"])
        _pip_install(["install", "-e", str(PROJECT_ROOT)])
    if not args.skip_service:
        try:
            command_service_restart(args)
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

        asyncio.run(_lavalink_health_check())
    except Exception as exc:  # pragma: no cover - optional diagnostics
        _echo(f"Lavalink health check failed: {exc}")
    _echo("Configuration looks good.")


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
    compose_file = DOCKER_DIR / "docker-compose.yml"
    if not compose_file.exists():
        raise CommandError("docker-compose.yml not found under infra/docker")
    base_cmd = ["docker", "compose", "-f", str(compose_file)]
    if args.action == "up":
        cmd = base_cmd + ["up", "-d", "--build"]
        if getattr(args, "remove_orphans", False):
            cmd.append("--remove-orphans")
    elif args.action == "down":
        cmd = base_cmd + ["down"]
    elif args.action == "pull":
        cmd = base_cmd + ["pull"]
    elif args.action == "logs":
        cmd = base_cmd + ["logs"]
        if getattr(args, "follow", False):
            cmd.append("-f")
    else:
        raise CommandError(f"Unknown docker action: {args.action}")
    _run(cmd)


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
    updater.add_argument("--skip-pull", action="store_true")
    updater.add_argument("--skip-deps", action="store_true")
    updater.add_argument("--skip-service", action="store_true")
    updater.set_defaults(func=command_update)

    runner = sub.add_parser("run", help="Run the bot in the foreground")
    runner.set_defaults(func=command_run)

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
