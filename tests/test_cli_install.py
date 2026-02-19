from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

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
    monkeypatch.setattr(cli.ops, "ensure_prerequisites", lambda **_: None)
    monkeypatch.setattr(cli.ops, "create_venv", lambda *_, **__: None)
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


def test_warn_port_conflicts_uses_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LAVALINK_PORT=4545\nPORT=9999\n", encoding="utf-8")

    monkeypatch.setattr(cli, "ENV_FILE", env_file)

    def fake_detect(ports: Iterable[int]) -> set[int]:
        assert 4545 in ports
        assert 9999 in ports
        return {4545}

    monkeypatch.setattr(cli.ops, "detect_port_conflicts", fake_detect)

    cli._warn_port_conflicts()

    output = capsys.readouterr().out
    assert "4545" in output
    assert "LAVALINK_PORT" in output


def test_command_update_check_skips_side_effects(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: list[str] = []
    monkeypatch.setattr(cli, "_run", lambda *_, **__: called.append("run"))
    monkeypatch.setattr(cli, "_pip_install", lambda *_, **__: called.append("pip"))
    monkeypatch.setattr(cli.ops, "control_service", lambda *_, **__: called.append("service"))

    args = argparse.Namespace(check=True, skip_pull=False, skip_deps=False, skip_service=False)
    cli.command_update(args)

    assert called == []
    assert "Update check complete" in capsys.readouterr().out
