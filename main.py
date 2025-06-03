from threading import Thread
import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
import logging
import sys
import atexit
import tempfile
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)
from flask_sock import Sock
import subprocess, psutil, shutil, re
from datetime import timedelta, datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# 1) Load .env first
load_dotenv()  # Load environment variables from .env file
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
GUILD_ID          = int(os.getenv('GUILD_ID',   '0'))
# …any other env vars…

# ── system binaries ───────────────────────────
SUDO       = shutil.which("sudo")       or "/usr/bin/sudo"
SYSTEMCTL  = shutil.which("systemctl")  or "/bin/systemctl"
JOURNALCTL = shutil.which("journalctl") or "/bin/journalctl"

# ── env & constants ──────────────────────────
REPO_DIR      = os.getenv("REPO_DIR", "/home/alex/ELBOT")
USERNAME       = os.getenv("WEB_USERNAME", "ALE")
PASSWORD       = os.getenv("WEB_PASSWORD", "ALEXIS00")
WEBHOOK_TOKEN  = os.getenv("WEBHOOK_TOKEN", "securetoken")

# ── Flask app ─────────────────────────────────
app = Flask(
    __name__,
    static_folder="./static/css",   # ./static/css/futuristic.css relative to main.py
    template_folder="./web/templates"
)

app.secret_key = os.getenv("FLASK_SECRET", "change_this_key")
secure_flag = os.getenv("FLASK_ENV") == "production"
app.config.update(
    SESSION_COOKIE_SECURE=secure_flag,  # Allow cookies over HTTP in non-production
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7)
)

sock = Sock(app)  # WebSocket support

# Add rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv('RATE_LIMIT_STORAGE_URI', 'redis://localhost:6379')  # use Redis by default
)

# Global variables for bot data
MAX_LOGS = 1000  # Maximum number of logs to keep in memory
bot_data = {
    "start_time": datetime.now(),
    "logs": [],
    "commands": {
        "f1": 0,
        "music": 0,
        "chat": 0
    }
}

def add_log(log_entry):
    """Add a log entry with rotation to prevent memory issues"""
    bot_data["logs"].append(log_entry)
    if len(bot_data["logs"]) > MAX_LOGS:
        bot_data["logs"] = bot_data["logs"][-MAX_LOGS:]

# Hash the password for secure storage
PASSWORD_HASH = generate_password_hash(PASSWORD)

# Function to check username and password
def authenticate(username, password):
    return username == USERNAME and check_password_hash(PASSWORD_HASH, password)

# 2) Create the bot
intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global error handler
@bot.event
async def on_command_error(ctx, error):
    """Handle errors globally for commands."""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Command not found. Use `!help` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Please check the command usage.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ This command is on cooldown. Try again in {round(error.retry_after, 2)} seconds.")
    else:
        logger.error(f"Unhandled error: {error}", exc_info=True)
        await ctx.send("❌ An unexpected error occurred. Please contact the admin.")

def run_flask():
    # Get port from environment variable with fallback to 8081
    port = int(os.getenv("ELBOT_WEB_PORT", "8081"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ╔════════════════════════════════════════════╗
# ☰  AUTH  ════════════════════════════════════
# ╚════════════════════════════════════════════╝
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if authenticate(username, password):
            session["logged_in"] = True
            # Assuming successful login grants admin role for this application's scope
            session["role"] = "admin"
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

@app.route("/admin")
def admin():
    if not session.get("logged_in") or session.get("role") != "admin":
        return redirect(url_for("login"))
    return render_template("admin.html")

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
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        proc.terminate()

# ╔════════════════════════════════════════════╗
# ☰  CONTROL ACTIONS                          
# ╚════════════════════════════════════════════╝
@app.route("/action/<cmd>")
def action(cmd):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    try:
        match cmd:
            case "start":
                subprocess.check_call([SUDO, SYSTEMCTL, "start", "elbot.service"])
            case "stop":
                subprocess.check_call([SUDO, SYSTEMCTL, "stop", "elbot.service"])
            case "restart":
                subprocess.check_call([SUDO, SYSTEMCTL, "restart", "elbot.service"])
            case "update":
                subprocess.check_call([f"{REPO_DIR}/deploy.sh"], shell=True)
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
    except subprocess.CalledProcessError as e:
        logger.error(f"Subprocess error: {e}")
        flash("Action failed", "danger")
    except Exception as e:
        # Catch any other unexpected errors during action execution
        logger.error(f"Unexpected error during action: {e}")
        flash("An unexpected error occurred", "danger")

    return redirect(url_for("index"))

# ╔════════════════════════════════════════════╗
# ☰  REST API                                 
# ╚════════════════════════════════════════════╝

def _auth():
    """Authentication helper that returns True if authenticated, or sends a 401 response"""
    if not session.get("logged_in"):
        return jsonify(error="unauthorized"), 401
    return True

@app.route("/api/system")
def api_system():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "uptime": int(datetime.now().timestamp() - psutil.boot_time())
    })

@app.route("/api/branches")
def api_branches():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    out = subprocess.check_output(["git", "-C", REPO_DIR, "branch", "a"], text=True)
    return jsonify(branches=[b.strip(" *\n") for b in out.splitlines()])

@app.route("/api/status")
def api_status():
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    try:
        active = subprocess.check_output([SYSTEMCTL, "is-active", "elbot.service"], text=True).strip()
    except subprocess.CalledProcessError:
        active = "unknown"
    return jsonify(status=active.capitalize())

@app.route("/api/logs")
def api_logs():
    """Return the latest bot logs."""
    logs = subprocess.check_output([JOURNALCTL, "-u", "elbot.service", "--no-pager", "-n", "50"]).decode("utf-8")
    return jsonify({"logs": logs.splitlines()})

@app.route("/switch-branch", methods=["POST"])
def switch_branch():
    if _auth() is not True:
        return _auth()
    data = request.get_json()
    branch = data.get("branch")
    if not branch:
        return jsonify({"error": "Branch name required"}), 400
    try:
        subprocess.check_call(["git", "-C", REPO_DIR, "checkout", branch])
        return jsonify({"message": f"Switched to branch {branch}"})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/env")
def api_env():
    if _auth() is not True:
        return _auth()
    # Return only safe environment variables
    safe_env = {
        "REPO_DIR": REPO_DIR,
        "BOT_VERSION": os.getenv("BOT_VERSION", "unknown"),
        "PYTHON_VERSION": os.getenv("PYTHON_VERSION", "unknown")
    }
    return jsonify(safe_env)

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
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    """Return the status of bot commands."""
    return jsonify(bot_data["commands"])

@app.route("/toggle_command/<command>", methods=["POST"])
def toggle_command(command):
    if not session.get("logged_in"):
        return jsonify({"error": "unauthorized"}), 401
    """Enable or disable a specific command."""
    if command in bot_data["commands"]:
        bot_data["commands"][command] = not bot_data["commands"][command]
        return jsonify({"status": "success", "command": command, "enabled": bot_data["commands"][command]}), 200
    return jsonify({"status": "error", "message": "Command not found."}), 404

@app.route("/bot/manage", methods=["POST"])
def manage_bot():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    action = request.form.get("action")
    if action == "start":
        subprocess.run([SUDO, SYSTEMCTL, "start", "elbot.service"])
        flash("Bot started successfully!", "success")
    elif action == "stop":
        subprocess.run([SUDO, SYSTEMCTL, "stop", "elbot.service"])
        flash("Bot stopped successfully!", "warning")
    elif action == "restart":
        subprocess.run([SUDO, SYSTEMCTL, "restart", "elbot.service"])
        flash("Bot restarted successfully!", "info")
    else:
        flash("Invalid action!", "danger")

    return redirect(url_for("index"))

@app.route("/bot/logs")
def bot_logs():
    """Render the logs page with the latest bot logs."""
    logs = subprocess.check_output([JOURNALCTL, "-u", "elbot.service", "--no-pager", "-n", "50"]).decode("utf-8")
    return render_template("logs.html", logs=logs.splitlines())

@app.route("/bot/analytics")
def bot_analytics():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    uptime = datetime.now() - bot_data["start_time"]
    command_stats = {cmd: count for cmd, count in bot_data["commands"].items()}
    return render_template("analytics.html", 
        uptime=uptime, 
        commands=bot_data["commands"],
        command_stats=command_stats
    )

@app.route("/health")
def health_check():
    """Health check endpoint to monitor the status of the web service and bot."""
    bot_status = "running" if psutil.pid_exists(os.getpid()) else "stopped"
    return jsonify({
        "web_service": "running",
        "bot_status": bot_status,
        "uptime": str(datetime.now() - bot_data["start_time"])
    })

@app.route("/metrics")
@limiter.limit("1 per second")
def metrics():
    """Metrics endpoint to provide resource usage and command stats."""
    try:
        cpu_usage = psutil.cpu_percent()
        memory_info = psutil.virtual_memory()
        memory_usage = memory_info.used // (1024 * 1024)  # Convert to MB

        return jsonify({
            "cpu_usage": cpu_usage,
            "memory_usage": memory_usage,
            "command_stats": bot_data["commands"],
            "uptime": str(datetime.now() - bot_data["start_time"])
        })
    except Exception as e:
        logger.error(f"Error fetching metrics: {e}")
        return jsonify({"error": "Failed to fetch metrics"}), 500

@app.route("/run-command", methods=["POST"])
def run_command():
    if not session.get("logged_in") or session.get("role") != "admin":
        return jsonify({"output": "Unauthorized"}), 403
    data = request.get_json()
    cmd = data.get("command", "").strip()
    # Only allow a safe set of commands (customize as needed)
    allowed_cmds = ["ls", "pwd", "whoami", "uptime", "df", "free", "ps", "top", "cat", "tail", "head"]
    if not cmd or cmd.split()[0] not in allowed_cmds:
        return jsonify({"output": "Command not allowed."}), 400
    try:
        result = subprocess.check_output(cmd, shell=True, text=True, timeout=5)
        return jsonify({"output": result})
    except Exception as e:
        return jsonify({"output": f"Error: {e}"}), 500

if __name__ == "__main__":
    # 5) Load your Cogs
    for ext in ('cogs.chat','cogs.music','cogs.dalle','cogs.localization','cogs.help','cogs.f1','cogs.pingptest'):
        try:
            bot.load_extension(ext)
            print(f'Loaded {ext}')
        except Exception as e:
            print(f'Failed to load {ext}:', e)

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot.run(DISCORD_BOT_TOKEN)












