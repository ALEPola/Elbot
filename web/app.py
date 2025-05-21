from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)
from flask_sock import Sock
import subprocess, os, psutil, shutil, re
from datetime import timedelta, datetime
from dotenv import load_dotenv

# ── system binaries ───────────────────────────
SUDO       = shutil.which("sudo")       or "/usr/bin/sudo"
SYSTEMCTL  = shutil.which("systemctl")  or "/bin/systemctl"
JOURNALCTL = shutil.which("journalctl") or "/bin/journalctl"

# ── env & constants ──────────────────────────
load_dotenv()
REPO_DIR      = "/home/alex/ELBOT"
USERNAME       = os.getenv("WEB_USERNAME", "ALE")
PASSWORD       = os.getenv("WEB_PASSWORD", "ALEXIS00")
WEBHOOK_TOKEN  = os.getenv("WEBHOOK_TOKEN", "securetoken")

# ── Flask app ─────────────────────────────────
app = Flask(
    __name__,
    static_folder="../static",   # ../static/futuristic.css
    template_folder="templates"
)
app.secret_key = os.getenv("FLASK_SECRET", "change_this_key")
app.config.update(
    SESSION_COOKIE_SECURE=False,   # flip True behind HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

sock = Sock(app)  # WebSocket support

# Global variables for bot data
bot_data = {
    "start_time": datetime.now(),
    "logs": [],
    "commands": {
        "f1": True,
        "music": True,
        "chat": True
    }
}

# ╔════════════════════════════════════════════╗
# ☰  AUTH  ════════════════════════════════════
# ╚════════════════════════════════════════════╝
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("username") == USERNAME and
            request.form.get("password") == PASSWORD):
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ╔════════════════════════════════════════════╗
# ☰  PAGES  ═══════════════════════════════════
# ╚════════════════════════════════════════════╝
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("index.html")

# ╔════════════════════════════════════════════╗
# ☰  WEBSOCKET  – live journal logs            
# ╚════════════════════════════════════════════╝
@sock.route("/ws/logs")
def ws_logs(ws):
    proc = subprocess.Popen(
        [JOURNALCTL, "-u", "elbot.service", "-f", "--no-pager", "--no-hostname"],
        stdout=subprocess.PIPE, text=True
    )
    try:
        for line in iter(proc.stdout.readline, ""):
            ws.send(line.rstrip())
    finally:
        proc.terminate()

# ╔════════════════════════════════════════════╗
# ☰  CONTROL ACTIONS                          
# ╚════════════════════════════════════════════╝
@app.route("/action/<cmd>")
def action(cmd):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    match cmd:
        case "start":   subprocess.call([SUDO, SYSTEMCTL, "start", "elbot.service"])
        case "stop":    subprocess.call([SUDO, SYSTEMCTL, "stop", "elbot.service"])
        case "restart": subprocess.call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
        case "update":  subprocess.call([f"{REPO_DIR}/deploy.sh"])
        case s if s.startswith("schedule:"):
            try:
                minute, hour = s.split(":",1)[1].split()
                cron = (
                    f"{minute} {hour} * * * /bin/bash {REPO_DIR}/deploy.sh && "
                    f"{SUDO} {SYSTEMCTL} restart elbot.service"
                )
                existing = subprocess.run("crontab -l || true", shell=True,
                                          capture_output=True, text=True).stdout
                if cron not in existing:
                    subprocess.run(f'(echo "{existing}"; echo "{cron}") | crontab -', shell=True)
                flash("Restart scheduled", "success")
            except ValueError:
                flash("Bad schedule format", "danger")
        case _:
            flash("Unknown action", "danger")
    return redirect(url_for("index"))

# ╔════════════════════════════════════════════╗
# ☰  REST API                                 
# ╚════════════════════════════════════════════╝

def _auth():
    return session.get("logged_in") or (jsonify(error="unauthorized"), 401)

@app.route("/api/system")
def api_system():
    if _auth() is not True: return _auth()
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": int(datetime.now().timestamp() - psutil.boot_time())
    })

@app.route("/api/branches")
def api_branches():
    if _auth() is not True: return _auth()
    out = subprocess.check_output(["git", "-C", REPO_DIR, "branch", "-a"], text=True)
    return jsonify(branches=[b.strip(" *\n") for b in out.splitlines()])

@app.route("/api/status")
def api_status():
    if _auth() is not True: return _auth()
    try:
        active = subprocess.check_output([SYSTEMCTL, "is-active", "elbot.service"], text=True).strip()
    except subprocess.CalledProcessError:
        active = "unknown"
    return jsonify(status=active.capitalize())

@app.route("/status")
def status():
    """Return bot status and system information."""
    uptime = datetime.now() - bot_data["start_time"]
    memory = psutil.virtual_memory()
    cpu = psutil.cpu_percent()
    disk = psutil.disk_usage("/")
    return jsonify({
        "uptime": str(uptime),
        "memory": {
            "total": memory.total // (1024 ** 3),
            "used": memory.used // (1024 ** 3),
            "percent": memory.percent
        },
        "cpu": cpu,
        "disk": {
            "total": disk.total // (1024 ** 3),
            "used": disk.used // (1024 ** 3),
            "percent": disk.percent
        }
    })

@app.route("/logs")
def logs():
    """Return the latest bot logs."""
    return jsonify({"logs": bot_data["logs"][-50:]})

@app.route("/commands")
def commands():
    """Return the status of bot commands."""
    return jsonify(bot_data["commands"])

@app.route("/toggle_command/<command>", methods=["POST"])
def toggle_command(command):
    """Enable or disable a specific command."""
    if command in bot_data["commands"]:
        bot_data["commands"][command] = not bot_data["commands"][command]
        return jsonify({"status": "success", "command": command, "enabled": bot_data["commands"][command]}), 200
    return jsonify({"status": "error", "message": "Command not found."}), 404

# dev runner
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=True)









