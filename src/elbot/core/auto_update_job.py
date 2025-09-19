"""Entry point for the scheduled auto-update job."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib import request, error

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOG_FILE = PROJECT_ROOT / "logs" / "auto-update.log"
PYTHON = sys.executable


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT / "src"))
    return subprocess.run(
        [PYTHON, "-m", "elbot.cli", *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def _append_log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _notify_failure(summary: str, details: str) -> None:
    webhook = os.environ.get("AUTO_UPDATE_WEBHOOK")
    if not webhook:
        return
    payload = {
        "content": f"**Elbot auto-update failed:** {summary}\n```{details.strip()[:1800]}```",
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    try:
        request.urlopen(req, timeout=10)
    except error.URLError:
        _append_log("Failed to dispatch webhook notification.")


def main() -> int:
    update = _run_cli(["update"])
    if update.returncode == 0:
        _append_log("Update succeeded." if not update.stdout else f"Update succeeded: {update.stdout.strip()}")
        restart = _run_cli(["service", "restart"])
        if restart.returncode == 0:
            _append_log("Service restart succeeded." if not restart.stdout else f"Service restart: {restart.stdout.strip()}")
            return 0
        detail = restart.stderr or restart.stdout or "Unknown service restart failure"
        _append_log(f"Service restart failed: {detail.strip()}")
        _notify_failure("service restart", detail)
        return restart.returncode or 1

    detail = update.stderr or update.stdout or "Unknown update failure"
    _append_log(f"Update failed: {detail.strip()}")
    _notify_failure("update run", detail)
    return update.returncode or 1


if __name__ == "__main__":  # pragma: no cover - exercised via systemd/cron
    sys.exit(main())
