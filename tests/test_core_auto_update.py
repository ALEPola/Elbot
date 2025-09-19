from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from elbot.core import auto_update


def test_ensure_systemd_units(tmp_path: Path) -> None:
    service, timer = auto_update.ensure_systemd_units(tmp_path, "/usr/bin/python3", "elbot.service")
    service_text = service.read_text(encoding="utf-8")
    timer_text = timer.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/python3 -m elbot.core.auto_update_job" in service_text
    assert "Unit=elbot-update.service" in timer_text


def test_enable_systemd_timer_invokes_systemctl(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(auto_update, "_systemctl", lambda: "/bin/systemctl")
    monkeypatch.setattr(auto_update, "systemd_supported", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    auto_update.enable_systemd_timer(tmp_path, "/usr/bin/python3", "elbot.service")

    assert any(cmd[1:3] == ["daemon-reload"] for cmd in calls)
    assert any(cmd[1:3] == ["enable", "--now"] and cmd[3] == auto_update.SYSTEMD_TIMER_NAME for cmd in calls)


def test_disable_systemd_timer(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(auto_update, "_systemctl", lambda: "/bin/systemctl")
    monkeypatch.setattr(auto_update, "systemd_supported", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    auto_update.disable_systemd_timer()

    assert any(cmd[1:3] == ["disable", "--now"] for cmd in calls)


def test_cron_entry_generation(tmp_path: Path) -> None:
    entry = auto_update.ensure_cron_entry(tmp_path, "/usr/bin/python3", "elbot.service")
    assert "elbot.core.auto_update_job" in entry
    assert "PYTHONPATH" in entry


def test_enable_cron_installs_entry(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="# comment\n0 3 * * * something elbot.core.auto_update_job\n")
        if cmd == ["crontab", "-"]:
            captured["input"] = kwargs.get("input", "")
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    auto_update.enable_cron(tmp_path, "/usr/bin/python3", "elbot.service")

    assert "elbot.core.auto_update_job" in captured["input"]


def test_cron_entry_present(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="# comment\n0 3 * * * something elbot.core.auto_update_job\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert auto_update.cron_entry_present() is True


def test_current_status_systemd(monkeypatch) -> None:
    status = SimpleNamespace(supported=True, enabled=True, active=True, last_trigger=None, next_run=None, error=None)
    monkeypatch.setattr(auto_update, "systemd_supported", lambda: True)
    monkeypatch.setattr(auto_update, "systemd_timer_status", lambda: status)

    result = auto_update.current_status()
    assert result.mode == "systemd"
    assert result.details is status


def test_current_status_cron(monkeypatch) -> None:
    monkeypatch.setattr(auto_update, "systemd_supported", lambda: False)
    monkeypatch.setattr(auto_update, "cron_supported", lambda: True)
    monkeypatch.setattr(auto_update, "cron_entry_present", lambda: True)

    result = auto_update.current_status()
    assert result.mode == "cron"
    assert result.cron_enabled is True
