from flask import Flask, render_template, redirect, url_for, request, session, flash, jsonify
import subprocess
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change_this_key")

# Set credentials either via environment variables or by default.
USERNAME = os.getenv("WEB_USERNAME", "ALE")
PASSWORD = os.getenv("WEB_PASSWORD", "ALEXIS00")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == USERNAME and password == PASSWORD:
            session["logged_in"] = True
            flash("Login successful.", "success")
            return redirect(url_for("index"))
        flash("Incorrect credentials. Please try again.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
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
        subprocess.call(["sudo", "systemctl", "restart", "elbot.service"])
    elif cmd == "start":
        subprocess.call(["sudo", "systemctl", "start", "elbot.service"])
    elif cmd == "stop":
        subprocess.call(["sudo", "systemctl", "stop", "elbot.service"])
    elif cmd == "update":
        subprocess.call(["/home/alex/ELBOT/deploy.sh"])
    return redirect(url_for("index"))

@app.route("/logs")
def logs():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("logs.html")

@app.route("/api/logs")
def api_logs():
    if not session.get("logged_in"):
         return jsonify({"error": "unauthorized"}), 401
    try:
         output = subprocess.check_output(
             ["journalctl", "-u", "elbot.service", "-n", "100", "--no-pager", "--no-hostname"],
             text=True
         )
    except subprocess.CalledProcessError as e:
         output = f"Error fetching logs: {e}"
    return jsonify({"logs": output})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)


