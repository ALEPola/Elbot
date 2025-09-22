from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from elbot import service_install


class _RunRecorder:
    def __init__(self, outputs: dict[tuple[str, ...], subprocess.CompletedProcess[str]]):
        self.outputs = outputs
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], check: bool = False, **kwargs):  # type: ignore[override]
        self.calls.append(cmd)
        key = tuple(cmd)
        result = self.outputs.get(key)
        if result is not None:
            return result
        return subprocess.CompletedProcess(cmd, 0)


@pytest.fixture
def record_runs(monkeypatch: pytest.MonkeyPatch) -> _RunRecorder:
    recorder = _RunRecorder({})
    monkeypatch.setattr(service_install.subprocess, "run", recorder)
    return recorder


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["systemctl"], returncode, stdout=stdout, stderr="")


def test_install_systemd_service_requires_when_lavalink_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_runs: _RunRecorder
) -> None:
    monkeypatch.setattr(service_install, "SYSTEMD_SERVICE_FILE", tmp_path / "elbot.service")
    record_runs.outputs[("systemctl", "list-unit-files", service_install.LAVALINK_UNIT)] = _completed(
        stdout=f"{service_install.LAVALINK_UNIT} enabled\n"
    )

    service_install.install_systemd_service(tmp_path, require_lavalink=True)

    content = (tmp_path / "elbot.service").read_text(encoding="utf-8")
    assert f"Requires={service_install.LAVALINK_UNIT}" in content
    assert f"After={service_install.LAVALINK_UNIT}" in content
    assert f"Wants={service_install.LAVALINK_UNIT}" not in content


def test_install_systemd_service_downgrades_when_lavalink_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_runs: _RunRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(service_install, "SYSTEMD_SERVICE_FILE", tmp_path / "elbot.service")
    record_runs.outputs[("systemctl", "list-unit-files", service_install.LAVALINK_UNIT)] = _completed(stdout="")

    service_install.install_systemd_service(tmp_path, require_lavalink=True)

    content = (tmp_path / "elbot.service").read_text(encoding="utf-8")
    assert f"Requires={service_install.LAVALINK_UNIT}" not in content
    assert f"Wants={service_install.LAVALINK_UNIT}" in content
    assert f"After={service_install.LAVALINK_UNIT}" in content

    captured = capsys.readouterr()
    assert "Lavalink systemd unit not found" in captured.err


def test_install_systemd_service_does_not_override_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record_runs: _RunRecorder
) -> None:
    monkeypatch.setattr(service_install, "SYSTEMD_SERVICE_FILE", tmp_path / "elbot.service")

    service_install.install_systemd_service(tmp_path)

    content = (tmp_path / "elbot.service").read_text(encoding="utf-8")
    assert "Environment=AUTO_LAVALINK" not in content
    assert "Environment=FFMPEG_PATH" not in content
