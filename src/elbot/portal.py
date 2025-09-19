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
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT_DIR / "elbot.log"
UPDATE_SCRIPT = ROOT_DIR / "scripts" / "run.sh"
SERVICE_NAME = os.environ.get("ELBOT_SERVICE", "elbot.service")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "0") == "1"

logger = logging.getLogger("elbot.portal")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "elbot-portal")


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


def _git_available() -> bool:
    return (
        _run_command(
            ["git", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        is not None
    )


def _systemctl_available() -> bool:
    return (
        _run_command(
            ["systemctl", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        is not None
    )


def _git_status_summary() -> dict[str, str]:
    if not _git_available():
        return {
            "branch": "Unavailable",
            "status": "Git is not available in this environment.",
        }

    branch_bytes = _check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT_DIR)
    branch = branch_bytes.decode().strip() if branch_bytes else "unknown"

    status_bytes = _check_output(["git", "status", "-sb"], cwd=ROOT_DIR)
    status = status_bytes.decode().strip() if status_bytes else "No status information available."
    return {"branch": branch, "status": status}


def _service_status() -> str:
    if not _systemctl_available():
        return "systemctl is not available in this environment."

    status_bytes = _check_output(["systemctl", "is-active", SERVICE_NAME])
    if status_bytes is None:
        return "Unable to determine service status."
    status = status_bytes.decode().strip()
    return {
        "active": "Service is active",
        "inactive": "Service is inactive",
        "failed": "Service has failed",
    }.get(status, f"Service status: {status}")


@app.route("/")
def index():
    git_info = _git_status_summary()
    service_info = _service_status()
    return render_template(
        "index.html",
        git_info=git_info,
        service_info=service_info,
    )


@app.route("/logs")
def view_logs():
    query = request.args.get("query", "").strip()
    auto_refresh = request.args.get("refresh", type=int)
    lines: list[str] = []
    download_available = LOG_FILE.exists()

    if download_available:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

    filtered_lines = lines[-200:]
    if query:
        filtered_lines = [line for line in lines if query.lower() in line.lower()][-200:]

    return render_template(
        "logs.html",
        logs="".join(filtered_lines),
        query=query,
        auto_refresh=auto_refresh if auto_refresh and auto_refresh > 0 else None,
        download_available=download_available,
    )


@app.route("/branch", methods=["GET", "POST"])
def branch():
    if request.method == "POST":
        branch_name = request.form.get("branch")
        pull_latest = request.form.get("pull_latest") == "on"
        confirmed = request.form.get("confirm_switch") == "on"

        if not branch_name:
            flash("Select a branch to switch to.", "warning")
            return redirect(url_for("branch"))

        status_bytes = _check_output(["git", "status", "--porcelain"], cwd=ROOT_DIR)
        working_tree_dirty = bool(status_bytes and status_bytes.strip())

        if working_tree_dirty and not confirmed:
            flash(
                "Uncommitted changes detected. Confirm the switch if you still want to proceed.",
                "warning",
            )
            return redirect(url_for("branch"))

        result = _run_command(["git", "checkout", branch_name], cwd=ROOT_DIR, check=True)
        if result is None:
            flash("Failed to switch branches. Ensure git is installed.", "error")
            return redirect(url_for("branch"))

        message = f"Switched to branch {branch_name}."

        if pull_latest:
            pull_result = _run_command(["git", "pull", "--ff-only"], cwd=ROOT_DIR, check=True)
            if pull_result is None:
                flash(
                    f"{message} Pull skipped because git is unavailable in this environment.",
                    "warning",
                )
                return redirect(url_for("branch"))
            message += " Pulled the latest changes."

        flash(message, "success")
        return redirect(url_for("branch"))

    if not _git_available():
        flash(
            "Git is not available in this environment. Install git to manage branches.",
            "error",
        )
        return render_template("branch.html", branches=[], remotes=[], current="Unavailable")

    current_bytes = _check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT_DIR)
    current = current_bytes.decode().strip() if current_bytes else "unknown"

    def build_refs(ref_path: str) -> list[dict[str, str]]:
        ref_bytes = _check_output(
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short)|%(objectname:short)|%(committerdate:relative)",
                ref_path,
            ],
            cwd=ROOT_DIR,
        )
        if not ref_bytes:
            return []
        refs: list[dict[str, str]] = []
        for line in ref_bytes.decode().splitlines():
            try:
                name, commit, date = line.split("|", 2)
            except ValueError:
                name, commit, date = line, "", ""
            refs.append({"name": name, "commit": commit, "date": date})
        return sorted(refs, key=lambda r: r["name"].lower())

    local_branches = build_refs("refs/heads")
    remote_branches = build_refs("refs/remotes")

    return render_template(
        "branch.html",
        branches=local_branches,
        remotes=remote_branches,
        current=current,
    )


@app.route("/update", methods=["POST"])
def update():
    result = _run_command(["bash", str(UPDATE_SCRIPT), "update"], cwd=ROOT_DIR, check=True)
    if result is None:
        flash("Update script unavailable. Ensure dependencies are installed.", "error")
    else:
        flash("Update completed successfully.", "success")
    return redirect(url_for("index"))


@app.route("/update_status")
def update_status():
    result = _run_command(["git", "remote", "update"], cwd=ROOT_DIR, check=True)
    if result is None:
        status = "Git is not available in this environment."
    else:
        output = _check_output(["git", "status", "-uno"], cwd=ROOT_DIR)
        status = output.decode() if output else "Git is not available in this environment."
    return render_template("update_status.html", status=status)


@app.route("/restart", methods=["POST"])
def restart():
    result = _run_command([
        "systemctl",
        "restart",
        SERVICE_NAME,
    ], check=True)
    if result is None:
        flash("Restart command unavailable. Install systemd to enable restarts.", "error")
    else:
        flash("Service restart triggered.", "success")
    return redirect(url_for("index"))


@app.route("/logs/download")
def download_logs():
    if not LOG_FILE.exists():
        flash("Log file is not available.", "error")
        return redirect(url_for("view_logs"))
    return send_file(LOG_FILE, as_attachment=True)


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
