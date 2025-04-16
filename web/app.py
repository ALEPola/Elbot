from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import subprocess
import os
import psutil
from datetime import timedelta, datetime
from dotenv import load_dotenv
import shlex
import shutil

SUDO = shutil.which("sudo")
SYSTEMCTL = shutil.which("systemctl")


load_dotenv()

app = Flask(__name__, static_folder="../static", template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET", "change_this_key")
app.permanent_session_lifetime = timedelta(days=7)

USERNAME = os.getenv("WEB_USERNAME", "ALE")
PASSWORD = os.getenv("WEB_PASSWORD", "ALEXIS00")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "securetoken")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == USERNAME and password == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("index.html")

@app.route("/action/<cmd>")
def action(cmd):
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    if cmd == "restart":
        subprocess.call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
    elif cmd == "start":
        subprocess.call([SUDO, SYSTEMCTL, "start", "elbot.service"])
    elif cmd == "stop":
        subprocess.call([SUDO, SYSTEMCTL, "stop", "elbot.service"])
    elif cmd == "update":
        subprocess.call(["/home/alex/ELBOT/deploy.sh"])
    elif cmd.startswith("schedule:"):
        hour_min = cmd.split(":")[1].strip()
        cron_line = f"{hour_min} * * * /bin/bash /home/alex/ELBOT/deploy.sh && {SUDO} {SYSTEMCTL} restart elbot.service"
        result = subprocess.run("crontab -l 2>/dev/null", shell=True, capture_output=True, text=True)
        lines = result.stdout.splitlines() if result.stdout else []
        if cron_line not in lines:
            lines.append(cron_line)
            new_cron = "\n".join(lines) + "\n"
            subprocess.run(f'echo "{new_cron}" | crontab -', shell=True)
    
    return redirect(url_for("index"))

@app.route("/api/logs")
def api_logs():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        output = subprocess.check_output([
            "journalctl", "-u", "elbot.service", "-n", "100", "--no-pager", "--no-hostname"
        ], text=True)
    except subprocess.CalledProcessError as e:
        output = f"Error fetching logs: {e}"
    return jsonify({"logs": output})

@app.route("/api/system")
def api_system():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": str(timedelta(seconds=int(datetime.now().timestamp() - psutil.boot_time())))
    })

@app.route("/api/status")
def api_status():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        status = subprocess.check_output([SYSTEMCTL, "is-active", "elbot.service"], text=True).strip()
        last_update = subprocess.check_output(["git", "log", "-1", "--format=%cd"], text=True).strip()
    except subprocess.CalledProcessError:
        status = "unknown"
        last_update = "unknown"
    return jsonify({"status": status.capitalize(), "last_update": last_update})


@app.route("/run-command", methods=["POST"])
def run_command():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    cmd = data.get("command", "")
    try:
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=10)
    except subprocess.CalledProcessError as e:
        output = e.output
    except Exception as e:
        output = str(e)
    return jsonify({"output": output})

@app.route("/switch-branch", methods=["POST"])
def switch_branch():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    branch = shlex.quote(data.get("branch"))
    try:
        subprocess.check_output(["git", "fetch", "origin"], cwd="/home/alex/ELBOT")
        subprocess.check_output(["git", "checkout", branch], cwd="/home/alex/ELBOT")
        subprocess.check_output([SUDO, SYSTEMCTL, "restart", "elbot.service"])
        return jsonify({"output": f"Switched to branch '{branch}' and restarted ELBOT."})
    except subprocess.CalledProcessError as e:
        return jsonify({"output": f"Error: {e.output}"})


@app.route("/api/env")
def get_env():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    keys = ["DISCORD_BOT_TOKEN", "WEB_USERNAME", "WEB_PASSWORD", "FLASK_SECRET", "YOUTUBE_COOKIE_HEADER"]
    env_vars = {k: ("****" if "TOKEN" in k or "PASSWORD" in k else os.getenv(k, "[unset]")) for k in keys}
    return jsonify(env_vars)

@app.route("/webhook/deploy", methods=["POST"])
def webhook_deploy():
    token = request.args.get("token")
    if token != WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 403
    subprocess.call(["/home/alex/ELBOT/deploy.sh"])
    subprocess.call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
    return jsonify({"status": "Deployed and restarted via webhook."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)




