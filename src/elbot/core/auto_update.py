"""Helpers for managing auto-update timers."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SYSTEMD_SERVICE_NAME = "elbot-update.service"
SYSTEMD_TIMER_NAME = "elbot-update.timer"
DEFAULT_ON_CALENDAR = "*-*-* 03:00:00"


@dataclass
class SystemdTimerStatus:
    supported: bool
    enabled: bool = False
    active: bool = False
    last_trigger: Optional[str] = None
    next_run: Optional[str] = None
    error: Optional[str] = None


def _systemctl() -> Optional[str]:
    return shutil.which("systemctl")


def systemd_supported() -> bool:
    return _systemctl() is not None


def systemd_timer_status() -> SystemdTimerStatus:
    if not systemd_supported():
        return SystemdTimerStatus(supported=False)

    status = SystemdTimerStatus(supported=True)
    systemctl = _systemctl()
    assert systemctl  # for mypy

    enabled = subprocess.run(
        [systemctl, "is-enabled", SYSTEMD_TIMER_NAME],
        text=True,
        capture_output=True,
    )
    status.enabled = enabled.returncode == 0

    active = subprocess.run(
        [systemctl, "is-active", SYSTEMD_TIMER_NAME],
        text=True,
        capture_output=True,
    )
    status.active = active.returncode == 0

    if status.enabled:
        show = subprocess.run(
            [systemctl, "show", SYSTEMD_TIMER_NAME, "--property=LastTriggerUSec,NextElapseUSecRealtime"],
            text=True,
            capture_output=True,
        )
        if show.returncode == 0 and show.stdout:
            for line in show.stdout.splitlines():
                if line.startswith("LastTriggerUSec="):
                    status.last_trigger = line.partition("=")[2].strip()
                elif line.startswith("NextElapseUSecRealtime="):
                    status.next_run = line.partition("=")[2].strip()
        else:
            status.error = show.stderr.strip() or show.stdout.strip()
    else:
        status.active = False
    return status


def _unit_dir(project_root: Path) -> Path:
    return project_root / "infra" / "systemd"


def _write_service_unit(project_root: Path, python_executable: str, service_name: str) -> Path:
    unit_dir = _unit_dir(project_root)
    unit_dir.mkdir(parents=True, exist_ok=True)
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "auto-update.log"
    service_path = unit_dir / SYSTEMD_SERVICE_NAME

    content = textwrap.dedent(
        f"""
        [Unit]
        Description=Elbot scheduled update
        After=network-online.target

        [Service]
        Type=oneshot
        WorkingDirectory={project_root}
        Environment=PYTHONPATH={project_root / 'src'}
        Environment=ELBOT_SERVICE={service_name}
        ExecStart={python_executable} -m elbot.core.auto_update_job
        StandardOutput=append:{log_file}
        StandardError=append:{log_file}

        [Install]
        WantedBy=multi-user.target
        """
    ).strip() + "\n"
    service_path.write_text(content, encoding="utf-8")
    return service_path


def _write_timer_unit(project_root: Path) -> Path:
    unit_dir = _unit_dir(project_root)
    timer_path = unit_dir / SYSTEMD_TIMER_NAME
    content = textwrap.dedent(
        f"""
        [Unit]
        Description=Run Elbot auto-update service daily

        [Timer]
        OnCalendar={DEFAULT_ON_CALENDAR}
        Persistent=true
        AccuracySec=1h
        Unit={SYSTEMD_SERVICE_NAME}

        [Install]
        WantedBy=timers.target
        """
    ).strip() + "\n"
    timer_path.write_text(content, encoding="utf-8")
    return timer_path


def ensure_systemd_units(project_root: Path, python_executable: str, service_name: str) -> tuple[Path, Path]:
    service_path = _write_service_unit(project_root, python_executable, service_name)
    timer_path = _write_timer_unit(project_root)
    return service_path, timer_path


def enable_systemd_timer(project_root: Path, python_executable: str, service_name: str) -> None:
    if not systemd_supported():
        raise RuntimeError("systemd is not available on this host")

    service_path, timer_path = ensure_systemd_units(project_root, python_executable, service_name)
    systemctl = _systemctl()
    assert systemctl

    # Link units so systemd is aware of them, then enable the timer.
    for unit in (service_path, timer_path):
        subprocess.run([systemctl, "link", str(unit)], check=False, text=True)

    subprocess.run([systemctl, "daemon-reload"], check=True, text=True)
    subprocess.run([systemctl, "enable", "--now", SYSTEMD_TIMER_NAME], check=True, text=True)


def disable_systemd_timer() -> None:
    if not systemd_supported():
        raise RuntimeError("systemd is not available on this host")

    systemctl = _systemctl()
    assert systemctl

    subprocess.run([systemctl, "disable", "--now", SYSTEMD_TIMER_NAME], check=True, text=True)
    subprocess.run([systemctl, "stop", SYSTEMD_SERVICE_NAME], check=False, text=True)


def cron_supported() -> bool:
    return shutil.which("crontab") is not None


def ensure_cron_entry(project_root: Path, python_executable: str, service_name: str) -> str:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "auto-update.log"
    path = f"{python_executable} -m elbot.core.auto_update_job >> {log_file} 2>&1"
    entry = f"0 3 * * * ELBOT_SERVICE={service_name} PYTHONPATH={project_root / 'src'} {path}"
    return entry


def enable_cron(project_root: Path, python_executable: str, service_name: str) -> None:
    entry = ensure_cron_entry(project_root, python_executable, service_name)
    cron = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    lines = [line for line in cron.stdout.splitlines() if "elbot.core.auto_update_job" not in line]
    lines.append(entry)
    cron_text = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=cron_text, text=True)
    if proc.returncode != 0:
        raise RuntimeError("Failed to install cron entry")


def disable_cron() -> None:
    cron = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    lines = [line for line in cron.stdout.splitlines() if "elbot.core.auto_update_job" not in line]
    cron_text = "\n".join(lines)
    subprocess.run(["crontab", "-"], input=cron_text, text=True, check=True)


def cron_entry_present() -> bool:
    cron = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if cron.returncode != 0:
        return False
    return any("elbot.core.auto_update_job" in line for line in cron.stdout.splitlines())


@dataclass
class AutoUpdateStatus:
    mode: str
    details: SystemdTimerStatus | None = None
    cron_enabled: bool = False


def current_status() -> AutoUpdateStatus:
    if systemd_supported():
        return AutoUpdateStatus(mode="systemd", details=systemd_timer_status())
    if cron_supported():
        return AutoUpdateStatus(mode="cron", cron_enabled=cron_entry_present())
    return AutoUpdateStatus(mode="disabled")
