from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import subprocess
import os
import psutil
from datetime import timedelta, datetime
from dotenv import load_dotenv
import shlex
import shutil
import re

# Helpers for subprocess paths
SUDO = shutil.which("sudo") or "/usr/bin/sudo"
SYSTEMCTL = shutil.which("systemctl") or "/bin/systemctl"
JOURNALCTL = shutil.which("journalctl") or "/bin/journalctl"

load_dotenv()

# Create Flask app
app = Flask(
    __name__,
    static_folder="../static",  # points to ELBOT/static (adjust if your css lives elsewhere)
    template_folder="templates"  # web/templates
)

app.secret_key = os.getenv("FLASK_SECRET", "change_this_key")
app.config.update(
    SESSION_COOKIE_SECURE=False,   # False for HTTP dev; set True behind HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

USERNAME = os.getenv("WEB_USERNAME", "ALE")
PASSWORD = os.getenv("WEB_PASSWORD", "ALEXIS00")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "securetoken")
GIT_DIR = "/home/alex/ELBOT"

# ----- Auth Routes -----
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == USERNAME and request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ----- Dashboard -----
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("index.html")

# ----- Control actions -----
@app.route("/action/<cmd>")
def action(cmd):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if cmd == "start":
        subprocess.call([SUDO, SYSTEMCTL, "start", "elbot.service"])
    elif cmd == "stop":
        subprocess.call([SUDO, SYSTEMCTL, "stop", "elbot.service"])
    elif cmd == "restart":
        subprocess.call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
    elif cmd == "update":
        subprocess.call([f"{GIT_DIR}/deploy.sh"])
    elif cmd.startswith("schedule:"):
        minute, hour = cmd.split(":", 1)[1].strip().split()
        if minute.isdigit() and hour.isdigit():
            cron = (
                f"{minute} {hour} * * * /bin/bash {GIT_DIR}/deploy.sh && "
                f"{SUDO} {SYSTEMCTL} restart elbot.service"
            )
            existing = subprocess.run("crontab -l || true", shell=True, capture_output=True, text=True).stdout
            if cron not in existing:
                new = existing + cron + "\n"
                subprocess.run(f'echo "{new}" | crontab -', shell=True)
    return redirect(url_for("index"))

# ----- API Endpoints -----
@app.route("/api/logs")
def api_logs():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    try:
        output = subprocess.check_output([
            JOURNALCTL, "-u", "elbot.service", "-n", "100", "--no-pager", "--no-hostname"
        ], text=True)
    except subprocess.CalledProcessError as e:
        output = f"Error: {e}"
    return jsonify(logs=output)

@app.route("/api/system")
def api_system():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": str(timedelta(seconds=int(datetime.now().timestamp() - psutil.boot_time())))
    })

@app.route("/api/status")
def api_status():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    try:
        status = subprocess.check_output([SYSTEMCTL, "is-active", "elbot.service"], text=True).strip()
        last_update = subprocess.check_output(["git", "-C", GIT_DIR, "log", "-1", "--format=%cd"], text=True).strip()
    except subprocess.CalledProcessError:
        status, last_update = "unknown", "unknown"
    return jsonify(status=status.capitalize(), last_update=last_update)

@app.route("/run-command", methods=["POST"])
def run_command():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    cmd = (request.get_json() or {}).get("command", "").split()
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=10)
    except Exception as e:
        out = str(e)
    return jsonify(output=out)

@app.route("/switch-branch", methods=["POST"])
def switch_branch():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    branch = (request.get_json() or {}).get("branch", "").strip()
    if not re.match(r"^[\w\-\./]+$", branch):
        return jsonify(error="invalid branch"), 400
    try:
        subprocess.check_output(["git", "-C", GIT_DIR, "fetch"], text=True)
        subprocess.check_output(["git", "-C", GIT_DIR, "checkout", branch], text=True)
        subprocess.check_output([SUDO, SYSTEMCTL, "restart", "elbot.service"], text=True)
        return jsonify(output=f"Switched to {branch} and restarted.")
    except subprocess.CalledProcessError as e:
        return jsonify(output=e.output)

@app.route("/api/env")
def get_env():
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    safe_keys = [
        "DISCORD_BOT_TOKEN",
        "WEB_USERNAME",
        "WEB_PASSWORD",
        "FLASK_SECRET"
    ]
    return jsonify({k: ("****" if "TOKEN" in k or "PASSWORD" in k else os.getenv(k, "")) for k in safe_keys})

@app.route("/webhook/deploy", methods=["POST"])
def webhook_deploy():
    if request.args.get("token", "") != WEBHOOK_TOKEN:
        return jsonify(error="unauthorized"), 403
    subprocess.call([f"{GIT_DIR}/deploy.sh"])
    subprocess.call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
    return jsonify(status="deployed")

# Dev runner
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=True)







