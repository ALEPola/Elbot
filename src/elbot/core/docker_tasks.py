"""Docker orchestration helpers for Elbot CLI refactor."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

RunFunc = Callable[[list[str]], object]


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
