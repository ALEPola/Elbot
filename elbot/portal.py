"""Simple web portal for managing Elbot."""

from __future__ import annotations

import os
import subprocess
import logging
import threading
import time
from pathlib import Path

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    url_for,
    render_template_string,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT_DIR / "elbot.log"
UPDATE_SCRIPT = ROOT_DIR / "scripts" / "update.sh"
SERVICE_NAME = os.environ.get("ELBOT_SERVICE", "elbot.service")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "0") == "1"

logger = logging.getLogger("elbot.portal")

app = Flask(__name__, template_folder="templates", static_folder="static")


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
            subprocess.run(["git", "checkout", branch], cwd=ROOT_DIR)
        return redirect(url_for("branch"))

    current = (
        subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT_DIR
        )
        .decode()
        .strip()
    )
    all_branches = (
        subprocess.check_output(
            [
                "git",
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ],
            cwd=ROOT_DIR,
        )
        .decode()
        .splitlines()
    )
    options = "".join(
        f'<option value="{b}" {"selected" if b == current else ""}>{b}</option>'
        for b in all_branches
    )
    return render_template("branch.html", options=options)


@app.route("/update", methods=["POST"])
def update():
    try:
        subprocess.run(["bash", str(UPDATE_SCRIPT)], cwd=ROOT_DIR, check=True)
    except subprocess.CalledProcessError as e:
        logger.error("update.sh failed: %s", e)
    return redirect(url_for("index"))


@app.route("/update_status")
def update_status():
    try:
        subprocess.run(["git", "remote", "update"], cwd=ROOT_DIR, check=True)
        status = subprocess.check_output(["git", "status", "-uno"], cwd=ROOT_DIR).decode()
    except subprocess.CalledProcessError as e:
        logger.error("Failed to check update status: %s", e)
        status = "error"
    return render_template_string("<pre>{{s}}</pre>", s=status)


@app.route("/restart", methods=["POST"])
def restart():
    try:
        subprocess.run(
            [
                "systemctl",
                "restart",
                SERVICE_NAME,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Failed to restart %s: %s", SERVICE_NAME, e)
    return redirect(url_for("index"))


def main():
    if AUTO_UPDATE:
        def updater():
            while True:
                try:
                    subprocess.run(["bash", str(UPDATE_SCRIPT)], cwd=ROOT_DIR, check=True)
                    subprocess.run(["systemctl", "restart", SERVICE_NAME], check=False)
                except Exception as e:
                    logger.error("Auto update failed: %s", e)
                time.sleep(86400)

        threading.Thread(target=updater, daemon=True).start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
