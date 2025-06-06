"""Simple web portal for managing Elbot."""

from __future__ import annotations

import os
import subprocess
import logging
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, url_for


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT_DIR / "elbot.log"
UPDATE_SCRIPT = ROOT_DIR / "scripts" / "update.sh"
SERVICE_NAME = os.environ.get("ELBOT_SERVICE", "elbot.service")

logger = logging.getLogger("elbot.portal")

app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(
        """
        <h1>Elbot Portal</h1>
        <ul>
          <li><a href="{{ url_for('view_logs') }}">View Logs</a></li>
          <li><a href="{{ url_for('branch') }}">Switch Branch</a></li>
        </ul>
        <form method="post" action="{{ url_for('update') }}">
          <button type="submit">Run update.sh</button>
        </form>
        <form method="post" action="{{ url_for('restart') }}">
          <button type="submit">Restart Service</button>
        </form>
        """
    )


@app.route("/logs")
def view_logs():
    lines = []
    if LOG_FILE.exists():
        with LOG_FILE.open("r") as f:
            lines = f.readlines()[-200:]
    return render_template_string(
        "<pre>{{logs}}</pre>",
        logs="".join(lines),
    )


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
        f'<option value="{b}" {"selected" if b==current else ""}>{b}</option>'
        for b in all_branches
    )
    return render_template_string(
        """
        <h2>Switch Branch</h2>
        <form method="post">
          <select name="branch">{{ options|safe }}</select>
          <button type="submit">Checkout</button>
        </form>
        """,
        options=options,
    )


@app.route("/update", methods=["POST"])
def update():
    try:
        subprocess.run(
            ["bash", str(UPDATE_SCRIPT)], cwd=ROOT_DIR, check=True
        )
    except subprocess.CalledProcessError as e:
        logger.error("update.sh failed: %s", e)
    return redirect(url_for("index"))


@app.route("/restart", methods=["POST"])
def restart():
    try:
        subprocess.run([
            "systemctl",
            "restart",
            SERVICE_NAME,
        ], check=True)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to restart %s: %s", SERVICE_NAME, e)
    return redirect(url_for("index"))


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
