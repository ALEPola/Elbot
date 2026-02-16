"""Unified core operational helpers for the Elbot CLI.

This module consolidates previously split helper modules for environment
management, networking, runtime/venv tasks, prerequisite checks, service
management, and Docker compose orchestration.
"""

from __future__ import annotations

import errno
import os
import shutil
import socket
import sys
from getpass import getpass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

# --- Shared type aliases ----------------------------------------------------

RunFunc = Callable[[list[str]], object]
EchoFunc = Callable[[str], None]
EnsureCommandFunc = Callable[[str], bool]
InputFunc = Callable[[str], str]
WhichFunc = Callable[[str], str | None]

EnvMap = dict[str, str]
InputFn = Callable[[str], str]

# --- Environment helpers ----------------------------------------------------


def sanitize_env_value(key: str, value: str) -> str:
    cleaned = str(value).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1]
    if key.upper() == "DISCORD_TOKEN":
        parts = cleaned.split(None, 1)
        if parts and parts[0].lower() == "bot":
            cleaned = parts[1] if len(parts) > 1 else ""
    return cleaned


def read_env(path: Path) -> EnvMap:
    values: EnvMap = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        values[key] = sanitize_env_value(key, value)
    return values


def write_env(path: Path, data: Mapping[str, str]) -> None:
    existing = read_env(path)
    for key, value in data.items():
        existing[key] = sanitize_env_value(key, value)
    lines = [f"{key}={sanitize_env_value(key, existing[key])}" for key in sorted(existing.keys())]
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def update_env_var(env_path: Path, key: str, value: str) -> None:
    pairs = read_env(env_path)
    pairs[key] = sanitize_env_value(key, value)
    write_env(env_path, pairs)


def ensure_env_file(env_path: Path, example_path: Path) -> None:
    if not env_path.exists() and example_path.exists():
        shutil.copy2(example_path, env_path)


def prompt_env(
    env_path: Path,
    example_path: Path,
    *,
    non_interactive: bool,
    overrides: Mapping[str, str] | None = None,
    required: Mapping[str, str] | None = None,
    optional: Mapping[str, str] | None = None,
    error_cls: type[Exception] = RuntimeError,
    input_fn: InputFn = input,
    secret_input_fn: InputFn = getpass,
) -> None:
    ensure_env_file(env_path, example_path)
    env_pairs = read_env(env_path)

    override_pairs: EnvMap = dict(overrides or {})

    keys_to_check: set[str] = set()
    if required:
        keys_to_check.update(required.keys())
    if optional:
        keys_to_check.update(optional.keys())

    for key in keys_to_check:
        if key not in override_pairs and key in os.environ:
            override_pairs[key] = os.environ[key]

    if override_pairs:
        for key, value in override_pairs.items():
            if value is not None:
                env_pairs[key] = sanitize_env_value(key, value)
        write_env(env_path, env_pairs)
        env_pairs = read_env(env_path)

    required_pairs = dict(required or {})
    optional_pairs = dict(optional or {})

    for key, prompt in required_pairs.items():
        if env_pairs.get(key):
            continue
        if non_interactive:
            raise error_cls(f"Missing required environment variable: {key}")
        value = secret_input_fn(f"{prompt}: ")
        sanitized = sanitize_env_value(key, value)
        if not sanitized:
            raise error_cls(f"{key} may not be empty")
        update_env_var(env_path, key, sanitized)
        env_pairs[key] = sanitized

    if non_interactive:
        return

    for key, prompt in optional_pairs.items():
        current = env_pairs.get(key, "")
        display = current or ("Elbot" if key == "ELBOT_USERNAME" else "skip")
        message = f"{prompt} [{display}]: "
        responder = secret_input_fn if "key" in key.lower() else input_fn
        value = responder(message)
        sanitized = sanitize_env_value(key, value)
        if sanitized:
            update_env_var(env_path, key, sanitized)
            env_pairs[key] = sanitized


# --- Network helpers --------------------------------------------------------

LOCAL_IPV4 = "127.0.0.1"
LOCAL_IPV6 = "::1"


def _is_port_open(port: int, *, timeout: float = 0.3) -> bool:
    candidates = [(socket.AF_INET, LOCAL_IPV4)]
    if socket.has_ipv6:
        candidates.append((socket.AF_INET6, LOCAL_IPV6))

    for family, host in candidates:
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(timeout)
        except OSError:
            continue
        try:
            destination = (host, port) if family == socket.AF_INET else (host, port, 0, 0)
            try:
                sock.connect(destination)
            except OSError as exc:
                err = getattr(exc, "errno", None)
                if err in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EADDRNOTAVAIL):
                    continue
                winerr = getattr(exc, "winerror", None)
                if winerr in (10061, 10060, 10051):
                    continue
                continue
            else:
                return True
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return False


def detect_port_conflicts(ports: Iterable[int]) -> list[int]:
    conflicts: list[int] = []
    for port in ports:
        if _is_port_open(port):
            conflicts.append(port)
    return conflicts


# --- Runtime helpers --------------------------------------------------------


def venv_python(venv_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_pip(venv_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        return venv_dir / "Scripts" / "pip.exe"
    return venv_dir / "bin" / "pip"


def create_venv(
    venv_dir: Path,
    *,
    force: bool = False,
    run: RunFunc,
    echo: EchoFunc | None = None,
    python_executable: str | None = None,
) -> None:
    python_executable = python_executable or sys.executable
    if venv_dir.exists():
        if not force:
            return
        shutil.rmtree(venv_dir)
    if echo:
        echo("Creating virtual environment (.venv)...")
    run([python_executable, "-m", "venv", str(venv_dir)])


def run_in_venv(
    args: Iterable[str],
    *,
    venv_dir: Path,
    is_windows: bool,
    run: RunFunc,
    error_cls: type[Exception] = RuntimeError,
):
    python = venv_python(venv_dir, is_windows=is_windows)
    if not python.exists():
        raise error_cls("virtual environment not found; run 'elbotctl install' first")
    return run([str(python), *list(args)])


def pip_install(
    args: Iterable[str],
    *,
    venv_dir: Path,
    is_windows: bool,
    run: RunFunc,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    pip = venv_pip(venv_dir, is_windows=is_windows)
    if not pip.exists():
        raise error_cls("pip not available in the virtual environment")
    run([str(pip), *list(args)])


# --- Prerequisite helpers ---------------------------------------------------

SUPPORTED_AUTO_INSTALL_PLATFORMS = {"linux", "darwin"}
DEFAULT_COMMANDS = ("ffmpeg", "java", "git")


def find_missing(commands: Iterable[str], ensure_command: EnsureCommandFunc) -> list[str]:
    return [cmd for cmd in commands if not ensure_command(cmd)]


def ensure_prerequisites(
    *,
    commands: Sequence[str] = DEFAULT_COMMANDS,
    install_packages: bool,
    non_interactive: bool,
    platform_name: str,
    ensure_command: EnsureCommandFunc,
    which: WhichFunc,
    run: RunFunc,
    echo: EchoFunc,
    input_fn: InputFunc = input,
) -> list[str]:
    missing = find_missing(commands, ensure_command)
    if not missing:
        return []

    echo("Missing prerequisites detected: " + ", ".join(missing))

    auto_installable = (
        install_packages
        and platform_name.lower() in SUPPORTED_AUTO_INSTALL_PLATFORMS
        and which("apt-get") is not None
    )

    if auto_installable:
        cmd = ["sudo", "apt-get", "install", "-y", *missing]
        should_install = True
        if not non_interactive:
            consent = input_fn(f"Install via {' '.join(cmd)}? [Y/n] ").strip().lower() or "y"
            if consent != "y":
                should_install = False
        if should_install:
            run(cmd)
            return []

    echo("Please install the missing tools manually and re-run the installer.")
    return missing


# --- Service helpers --------------------------------------------------------

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


# --- Docker helpers ---------------------------------------------------------


def ensure_compose_file(
    docker_dir: Path,
    *,
    error_cls: type[Exception] = RuntimeError,
) -> Path:
    compose_file = docker_dir / "docker-compose.yml"
    if compose_file.exists():
        return compose_file
    raise error_cls("docker-compose.yml not found under infra/docker")


def run_compose_action(
    action: str,
    *,
    docker_dir: Path,
    run: RunFunc,
    remove_orphans: bool = False,
    follow: bool = False,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    compose_file = ensure_compose_file(docker_dir, error_cls=error_cls)
    base_cmd = ["docker", "compose", "-f", str(compose_file)]

    if action == "up":
        cmd = base_cmd + ["up", "-d", "--build"]
        if remove_orphans:
            cmd.append("--remove-orphans")
    elif action == "down":
        cmd = base_cmd + ["down"]
    elif action == "pull":
        cmd = base_cmd + ["pull"]
    elif action == "logs":
        cmd = base_cmd + ["logs"]
        if follow:
            cmd.append("-f")
    else:
        raise error_cls(f"Unknown docker action: {action}")

    run(cmd)
