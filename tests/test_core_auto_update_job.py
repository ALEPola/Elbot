from __future__ import annotations

import os
import subprocess
from pathlib import Path

from elbot.core import auto_update_job


def test_main_success(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run_cli(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='ok')

    log_file = tmp_path / 'auto-update.log'
    monkeypatch.setattr(auto_update_job, '_run_cli', fake_run_cli)
    monkeypatch.setattr(auto_update_job, 'LOG_FILE', log_file)
    notifications = []
    monkeypatch.setattr(auto_update_job, '_notify_failure', lambda summary, details: notifications.append((summary, details)))

    rc = auto_update_job.main()

    assert rc == 0
    assert calls == [["update"], ["service", "restart"]]
    assert notifications == []
    contents = log_file.read_text(encoding='utf-8')
    assert 'Update succeeded' in contents
    assert 'Service restart' in contents


def test_main_update_failure(monkeypatch, tmp_path: Path) -> None:
    log_file = tmp_path / 'auto-update.log'
    sequence = [subprocess.CompletedProcess(['update'], 1, stdout='', stderr='boom')]

    def fake_run_cli(args):
        return sequence.pop(0)

    notifications = []

    monkeypatch.setattr(auto_update_job, '_run_cli', fake_run_cli)
    monkeypatch.setattr(auto_update_job, 'LOG_FILE', log_file)
    monkeypatch.setattr(auto_update_job, '_notify_failure', lambda summary, details: notifications.append((summary, details)))
    monkeypatch.setenv('AUTO_UPDATE_WEBHOOK', 'https://example.com/hook')

    rc = auto_update_job.main()

    assert rc == 1
    assert notifications and notifications[0][0] == 'update run'
    assert 'Update failed' in log_file.read_text(encoding='utf-8')


def test_main_restart_failure(monkeypatch, tmp_path: Path) -> None:
    log_file = tmp_path / 'auto-update.log'
    sequence = [
        subprocess.CompletedProcess(['update'], 0, stdout='', stderr=''),
        subprocess.CompletedProcess(['service', 'restart'], 2, stdout='', stderr='bad restart'),
    ]

    def fake_run_cli(args):
        return sequence.pop(0)

    notifications = []

    monkeypatch.setattr(auto_update_job, '_run_cli', fake_run_cli)
    monkeypatch.setattr(auto_update_job, 'LOG_FILE', log_file)
    monkeypatch.setattr(auto_update_job, '_notify_failure', lambda summary, details: notifications.append((summary, details)))
    monkeypatch.setenv('AUTO_UPDATE_WEBHOOK', 'https://example.com/hook')

    rc = auto_update_job.main()

    assert rc == 2
    assert notifications and notifications[0][0] == 'service restart'
    text = log_file.read_text(encoding='utf-8')
    assert 'Service restart failed' in text
