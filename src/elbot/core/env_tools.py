"""Environment helpers for Elbot CLI refactor."""

from __future__ import annotations

import os
import shutil
from getpass import getpass
from pathlib import Path
from typing import Callable, Mapping

EnvMap = dict[str, str]
InputFn = Callable[[str], str]


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
        values[key.strip()] = value.strip()
    return values


def write_env(path: Path, data: Mapping[str, str]) -> None:
    existing = read_env(path)
    existing.update(data)
    lines = [f"{key}={existing[key]}" for key in sorted(existing.keys())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_var(env_path: Path, key: str, value: str) -> None:
    pairs = read_env(env_path)
    pairs[key] = value
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
                env_pairs[key] = value
        write_env(env_path, env_pairs)
        env_pairs = read_env(env_path)

    required_pairs = dict(required or {})
    optional_pairs = dict(optional or {})

    for key, prompt in required_pairs.items():
        if env_pairs.get(key):
            continue
        if non_interactive:
            raise error_cls(f"Missing required environment variable: {key}")
        value = secret_input_fn(f"{prompt}: ").strip()
        if not value:
            raise error_cls(f"{key} may not be empty")
        update_env_var(env_path, key, value)
        env_pairs[key] = value

    if non_interactive:
        return

    for key, prompt in optional_pairs.items():
        current = env_pairs.get(key, "")
        display = current or ("Elbot" if key == "ELBOT_USERNAME" else "skip")
        message = f"{prompt} [{display}]: "
        responder = secret_input_fn if "key" in key.lower() else input_fn
        value = responder(message).strip()
        if value:
            update_env_var(env_path, key, value)
            env_pairs[key] = value
