from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pytest

from elbot import cli
from elbot.core.auto_update import AutoUpdateStatus, SystemdTimerStatus


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


def test_warn_port_conflicts_uses_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LAVALINK_PORT=4545\nPORT=9999\n", encoding="utf-8")

    monkeypatch.setattr(cli, "ENV_FILE", env_file)

    def fake_detect(ports: Iterable[int]) -> set[int]:
        assert 4545 in ports
        assert 9999 in ports
        return {4545}

    monkeypatch.setattr(cli.network, "detect_port_conflicts", fake_detect)

    cli._warn_port_conflicts()

    output = capsys.readouterr().out
    assert "4545" in output
    assert "LAVALINK_PORT" in output


def test_command_auto_update_enable_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded: dict[str, object] = {}

    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "_venv_python", lambda: tmp_path / ".venv" / "bin" / "python")

    def fake_enable(root: Path, python_executable: str, service_name: str) -> str:
        recorded["root"] = root
        recorded["python"] = python_executable
        recorded["service"] = service_name
        return "systemd"

    monkeypatch.setattr(cli.deploy, "enable_auto_update", fake_enable)

    args = argparse.Namespace(root=None, python=None, service_name=None)
    cli.command_auto_update_enable(args)

    captured = capsys.readouterr().out
    assert "systemd" in captured
    assert recorded["root"] == tmp_path
    assert recorded["python"] == str(tmp_path / ".venv" / "bin" / "python")
    assert recorded["service"] == cli.deploy.SERVICE_NAME_DEFAULT


def test_command_auto_update_enable_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    values: dict[str, object] = {}

    def fake_enable(root: Path, python_executable: str, service_name: str) -> str:
        values["root"] = root
        values["python"] = python_executable
        values["service"] = service_name
        return "cron"

    monkeypatch.setattr(cli.deploy, "enable_auto_update", fake_enable)

    args = argparse.Namespace(root=tmp_path, python="/opt/python", service_name="custom.service")
    cli.command_auto_update_enable(args)

    assert values == {"root": tmp_path, "python": "/opt/python", "service": "custom.service"}


def test_command_auto_update_disable(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.deploy, "disable_auto_update", lambda: "cron")

    cli.command_auto_update_disable(argparse.Namespace())

    output = capsys.readouterr().out
    assert "cron" in output


def test_command_auto_update_status_with_details(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    status = AutoUpdateStatus(
        mode="systemd",
        details=SystemdTimerStatus(
            supported=True,
            enabled=True,
            active=True,
            last_trigger="2024-06-01 03:00",
            next_run="2024-06-02 03:00",
            error=None,
        ),
    )

    monkeypatch.setattr(cli.deploy, "auto_update_status", lambda: status)

    cli.command_auto_update_status(argparse.Namespace())

    captured = capsys.readouterr().out
    assert "systemd" in captured
    assert "enabled: yes" in captured
    assert "next run" in captured


def test_command_auto_update_status_disabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli.deploy, "auto_update_status", lambda: AutoUpdateStatus(mode="disabled"))

    cli.command_auto_update_status(argparse.Namespace())

    captured = capsys.readouterr().out
    assert "disabled" in captured.lower()
