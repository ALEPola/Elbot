from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from elbot import cli


def test_command_install_non_interactive_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    env_example.write_text("", encoding="utf-8")

    monkeypatch.setattr(cli, "ENV_FILE", env_file)
    monkeypatch.setattr(cli, "ENV_EXAMPLE", env_example)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "VENV_DIR", tmp_path / ".venv")

    monkeypatch.setattr(cli, "_warn_port_conflicts", lambda: None)
    monkeypatch.setattr(cli.prerequisites, "ensure_prerequisites", lambda **_: None)
    monkeypatch.setattr(cli.runtime, "create_venv", lambda *_, **__: None)
    monkeypatch.setattr(cli, "_pip_install", lambda *_, **__: None)
    monkeypatch.setattr(cli, "_run_in_venv", lambda *_, **__: None)

    monkeypatch.setenv("ELBOT_DISCORD_TOKEN", "secret")
    monkeypatch.setenv("ELBOT_OPENAI_KEY", "open-ai")

    args = argparse.Namespace(
        env_file=None,
        install_system_packages=False,
        non_interactive=True,
        recreate=False,
        no_service=True,
        require_lavalink=False,
    )

    cli.command_install(args)

    env_contents = env_file.read_text(encoding="utf-8")
    assert "DISCORD_TOKEN=secret" in env_contents
    assert "OPENAI_API_KEY=open-ai" in env_contents
