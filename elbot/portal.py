"""Simple web portal for managing Elbot."""

from __future__ import annotations

import os
import subprocess
import logging
import threading
import time
from pathlib import Path
from typing import Iterable

from flask import (
    Flask,
    redirect,
    render_template,
    render_template_string,
    request,
    url_for,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT_DIR / "elbot.log"
UPDATE_SCRIPT = ROOT_DIR / "scripts" / "run.sh"
SERVICE_NAME = os.environ.get("ELBOT_SERVICE", "elbot.service")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "0") == "1"

logger = logging.getLogger("elbot.portal")

app = Flask(__name__, template_folder="templates", static_folder="static")


def _log_missing(command: str) -> None:
    """Log a warning when an external command is unavailable."""

    logger.warning("Command %s is not available in this environment", command)


def _run_command(args: Iterable[str], **kwargs) -> subprocess.CompletedProcess | None:
    """Run a subprocess command returning ``None`` if it is unavailable."""

    try:
        return subprocess.run(args, **kwargs)
    except FileNotFoundError:
        _log_missing(args[0])
        return None
    except subprocess.CalledProcessError as exc:
        logger.error("Command %s failed: %s", args[0], exc)
        return None


def _check_output(args: Iterable[str], **kwargs) -> bytes | None:
    """Wrapper around :func:`subprocess.check_output` with graceful fallbacks."""

    try:
        return subprocess.check_output(args, **kwargs)
    except FileNotFoundError:
        _log_missing(args[0])
        return None
    except subprocess.CalledProcessError as exc:
        logger.error("Command %s failed: %s", args[0], exc)
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/logs")
def view_logs():
    lines = []
    if LOG_FILE.exists():
        with LOG_FILE.open("r") as f:
            lines = f.readlines()[-200:]
    return render_template("logs.html", logs="".join(lines))


@app.route("/branch", methods=["GET", "POST"])
def branch():
    if request.method == "POST":
        branch = request.form.get("branch")
        if branch:
            _run_command(["git", "checkout", branch], cwd=ROOT_DIR, check=True)
        return redirect(url_for("branch"))

    current_bytes = _check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT_DIR)
    current = current_bytes.decode().strip() if current_bytes else "unknown"

    branch_bytes = _check_output(
        [
            "git",
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        ],
        cwd=ROOT_DIR,
    )
    all_branches = branch_bytes.decode().splitlines() if branch_bytes else []

    if all_branches:
        options = "".join(
            f'<option value="{b}" {"selected" if b == current else ""}>{b}</option>'
            for b in all_branches
        )
    else:
        options = '<option value="" disabled>Git is not available in this environment.</option>'
    return render_template("branch.html", options=options)


@app.route("/update", methods=["POST"])
def update():
    _run_command(["bash", str(UPDATE_SCRIPT), "update"], cwd=ROOT_DIR, check=True)
    return redirect(url_for("index"))


@app.route("/update_status")
def update_status():
    result = _run_command(["git", "remote", "update"], cwd=ROOT_DIR, check=True)
    if result is None:
        status = "Git is not available in this environment."
    else:
        output = _check_output(["git", "status", "-uno"], cwd=ROOT_DIR)
        status = output.decode() if output else "Git is not available in this environment."
    return render_template_string("<pre>{{s}}</pre>", s=status)


@app.route("/restart", methods=["POST"])
def restart():
    _run_command([
        "systemctl",
        "restart",
        SERVICE_NAME,
    ], check=True)
    return redirect(url_for("index"))


def main():
    if AUTO_UPDATE:
        def updater():
            while True:
                try:
                    _run_command(["bash", str(UPDATE_SCRIPT), "update"], cwd=ROOT_DIR, check=True)
                    _run_command(["systemctl", "restart", SERVICE_NAME], check=False)
                except Exception as e:
                    logger.error("Auto update failed: %s", e)
                time.sleep(86400)

        threading.Thread(target=updater, daemon=True).start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
