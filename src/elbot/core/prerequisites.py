"""Prerequisite detection and optional installation helpers."""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

EchoFunc = Callable[[str], None]
EnsureCommandFunc = Callable[[str], bool]
RunFunc = Callable[[list[str]], object]
InputFunc = Callable[[str], str]
WhichFunc = Callable[[str], str | None]

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
