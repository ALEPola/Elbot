"""Web portal for installing and managing Elbot."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from openai import OpenAI


from .core import auto_update
from .config import Config
from .music import CookieManager, DiagnosticsService, PlaybackMetrics

ROOT_DIR = Config.BASE_DIR
ENV_FILE = ROOT_DIR / ".env"
LOG_FILE = ROOT_DIR / "logs" / "elbot.log"
UPDATE_LOG_FILE = ROOT_DIR / "logs" / "update.log"
AUTO_UPDATE_LOG_FILE = ROOT_DIR / "logs" / "auto-update.log"
SERVICE_NAME = os.environ.get("ELBOT_SERVICE", "elbot.service")
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "0") == "1"

REQUIRED_KEYS = ["DISCORD_TOKEN"]
OPTIONAL_KEYS = [
    "OPENAI_API_KEY",
    "LAVALINK_HOST",
    "LAVALINK_PORT",
    "LAVALINK_PASSWORD",
    "AUTO_LAVALINK",
    "AUTO_UPDATE_WEBHOOK",
]

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("ELBOT_PORTAL_SECRET", "change-me")

logger = logging.getLogger("elbot.portal")

_DIAGNOSTICS_COOKIES = CookieManager()
_DIAGNOSTICS_METRICS = PlaybackMetrics()


def _read_env(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _write_env(path: Path, values: Dict[str, str]) -> None:
    data = _read_env(path)
    data.update(values)
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _env_values() -> Dict[str, str]:
    return _read_env(ENV_FILE)


def _env_snapshot() -> Dict[str, str]:
    data = _env_values()
    for key, value in os.environ.items():
        data[key] = value
    return data


def _auto_lavalink_enabled() -> bool:
    value = _env_snapshot().get("AUTO_LAVALINK", "")
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes"}


def _diagnostics_service(
    env: Dict[str, str],
) -> Tuple[DiagnosticsService, Dict[str, Any]]:
    host = env.get("LAVALINK_HOST") or "localhost"
    port_str = env.get("LAVALINK_PORT") or "0"
    password = env.get("LAVALINK_PASSWORD") or "youshallnotpass"
    secure_flag = str(env.get("LAVALINK_SSL", "false")).strip().lower()
    secure_enabled = secure_flag in {"1", "true", "yes"}
    try:
        port = int(port_str)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid LAVALINK_PORT value; expected integer.") from exc

    service = DiagnosticsService(
        host=host,
        port=port,
        password=password,
        secure=secure_enabled,
        cookies=_DIAGNOSTICS_COOKIES,
        metrics=_DIAGNOSTICS_METRICS,
    )
    return service, {
        "host": host,
        "port": port,
        "secure": secure_enabled,
    }


def _collect_diagnostics() -> Tuple[Dict[str, Any] | None, str | None, int]:
    env = _env_snapshot()
    try:
        service, meta = _diagnostics_service(env)
    except ValueError as exc:
        return None, str(exc), 400

    try:
        report = asyncio.run(service.collect())
    except asyncio.TimeoutError:
        return None, "Timed out while contacting the Lavalink server.", 504
    except Exception as exc:  # pragma: no cover - diagnostic failures surfaced to UI
        logger.warning("Diagnostics collection failed: %s", exc, exc_info=True)
        return None, f"Failed to collect diagnostics: {exc}", 502

    payload = asdict(report)
    payload.update(
        {
            "lavalink_host": meta["host"],
            "lavalink_port": meta["port"],
            "lavalink_secure": meta["secure"],
        }
    )
    return payload, None, 200


def _is_configured() -> bool:
    env = _env_values()
    return all(env.get(key) for key in REQUIRED_KEYS)


def _openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or _env_values().get("OPENAI_API_KEY", "")


def _run_elbotctl(args: Iterable[str]) -> subprocess.CompletedProcess | None:
    cmd = [sys.executable, "-m", "elbot.cli", *args]
    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT_DIR / "src"))
    try:
        return subprocess.run(
            cmd, cwd=ROOT_DIR, text=True, capture_output=True, env=env, check=True
        )
    except subprocess.CalledProcessError as exc:
        flash(exc.stderr or exc.stdout or str(exc), "error")
        return exc
    except FileNotFoundError:
        flash("Python interpreter not found while invoking elbotctl.", "error")
        return None


def _ensure_logs_dir() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    return "".join(path.read_text(encoding="utf-8").splitlines(True)[-max_lines:])


@app.context_processor
def inject_flags():
    return {
        "configured": _is_configured(),
        "auto_update_status": auto_update.current_status(),
        "legacy_auto_update": AUTO_UPDATE,
        "auto_update": AUTO_UPDATE,
        "auto_lavalink_enabled": _auto_lavalink_enabled(),
    }


@app.route("/")
def index():
    if not _is_configured() and not app.config.get("TESTING"):
        return redirect(url_for("setup"))
    return render_template("index.html")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    values = _env_values()
    if request.method == "POST":
        discord_token = request.form.get("discord_token", "").strip()
        openai_key = request.form.get("openai_api_key", "").strip()
        auto_update_webhook = request.form.get("auto_update_webhook", "").strip()
        auto_lavalink = "1" if request.form.get("auto_lavalink") == "on" else "0"
        lavalink_host = request.form.get("lavalink_host", "").strip() or "localhost"
        lavalink_port = request.form.get("lavalink_port", "").strip() or "0"
        lavalink_password = (
            request.form.get("lavalink_password", "").strip() or "youshallnotpass"
        )

        if not discord_token:
            flash("Discord token is required.", "error")
        else:
            updates = {
                "DISCORD_TOKEN": discord_token,
                "OPENAI_API_KEY": openai_key,
                "AUTO_UPDATE_WEBHOOK": auto_update_webhook,
                "AUTO_LAVALINK": auto_lavalink,
            }
            if auto_lavalink == "0":
                updates.update(
                    {
                        "LAVALINK_HOST": lavalink_host,
                        "LAVALINK_PORT": lavalink_port,
                        "LAVALINK_PASSWORD": lavalink_password,
                    }
                )
            _write_env(ENV_FILE, updates)
            flash("Configuration saved. Installing dependencies...", "info")
            result = _run_elbotctl(["install", "--non-interactive", "--no-service"])
            if result and getattr(result, "returncode", 0) == 0:
                flash(result.stdout or "Installation complete.", "success")
                return redirect(url_for("index"))
            else:
                flash(getattr(result, "stdout", "") or "Installation failed.", "error")
        values = _env_values()
    return render_template("setup.html", values=values)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    values = _env_values()
    if request.method == "POST":
        updates = {}
        for key in REQUIRED_KEYS + OPTIONAL_KEYS:
            if key in request.form:
                updates[key] = request.form.get(key, "").strip()
        _write_env(ENV_FILE, updates)
        flash("Settings updated.", "success")
        return redirect(url_for("settings"))
    return render_template("settings.html", values=values)


@app.route("/logs")
def view_logs():
    _ensure_logs_dir()
    logs = ""
    if LOG_FILE.exists():
        logs = "".join(LOG_FILE.read_text(encoding="utf-8").splitlines(True)[-200:])
    return render_template("logs.html", logs=logs, ai_enabled=bool(_openai_api_key()))


@app.route("/api/ytcheck")
def api_ytcheck():
    if not _auto_lavalink_enabled():
        return (
            jsonify(
                {
                    "status": "error",
                    "error": "AUTO_LAVALINK is disabled; diagnostics are unavailable.",
                }
            ),
            400,
        )

    payload, error, status = _collect_diagnostics()
    if payload is not None:
        return jsonify({"status": "ok", "data": payload}), 200

    message = error or "Failed to collect diagnostics."
    return jsonify({"status": "error", "error": message}), status


def _summarize_logs_with_ai(log_text: str, *, api_key: str) -> str:
    """Summarize log text using the configured OpenAI model."""

    trimmed = log_text[-8000:]
    client = OpenAI(api_key=api_key)
    completion = client.chat.completions.create(
        model=Config.OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant that summarizes application logs. "
                    "Highlight key errors, warnings, and suggested follow-up actions in a concise bullet list."
                ),
            },
            {"role": "user", "content": trimmed},
        ],
        max_tokens=250,
    )
    return (completion.choices[0].message.content or "").strip()


@app.route("/logs/summary", methods=["POST"])
def logs_summary():
    api_key = _openai_api_key()
    if not api_key:
        return jsonify({"error": "OpenAI API key is not configured."}), 400

    log_text = _read_tail(LOG_FILE)
    if not log_text.strip():
        return jsonify({"error": "No logs available to summarize."}), 400

    try:
        summary = _summarize_logs_with_ai(log_text, api_key=api_key)
    except Exception as exc:  # pragma: no cover - surfaced via JSON error
        logger.error("Failed to summarize logs with OpenAI: %s", exc, exc_info=True)
        return jsonify({"error": "Failed to summarize logs with OpenAI."}), 502

    if not summary:
        return jsonify({"error": "OpenAI returned an empty summary."}), 502

    return jsonify({"summary": summary})


@app.route("/update-status")
@app.route("/update_status")
def update_status():
    _ensure_logs_dir()
    errors = []

    git_status = ""
    try:
        git_result = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        git_status = ""
        errors.append("Git is not available in this environment.")
    else:
        git_status = (git_result.stdout or git_result.stderr or "").strip()
        if git_result.returncode != 0:
            errors.append("Failed to gather git status.")

    elbotctl_output = ""
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONPATH", str(ROOT_DIR / "src"))
        result = subprocess.run(
            [sys.executable, "-m", "elbot.cli", "update", "--check"],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        elbotctl_output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0 and not elbotctl_output:
            errors.append("Update status command exited with a non-zero status.")
    except FileNotFoundError:
        errors.append("Python interpreter not found while invoking elbotctl.")

    update_log = (
        _read_tail(UPDATE_LOG_FILE)
        or _read_tail(AUTO_UPDATE_LOG_FILE)
        or _read_tail(LOG_FILE)
    )
    if not update_log:
        errors.append("No update log entries were found.")

    return render_template(
        "update_status.html",
        git_status=git_status,
        update_log=update_log,
        elbotctl_output=elbotctl_output,
        auto_update=AUTO_UPDATE,
        errors=errors,
    )


@app.route("/update", methods=["POST"])
def update():
    result = _run_elbotctl(["update"])
    if result and getattr(result, "returncode", 0) == 0:
        flash(result.stdout or "Update completed.", "success")
    return redirect(url_for("index"))


@app.route("/auto-update", methods=["POST"])
def toggle_auto_update():
    action = request.form.get("action", "").lower()
    next_url = request.form.get("next") or url_for("index")
    try:
        if action == "enable":
            if auto_update.systemd_supported():
                auto_update.enable_systemd_timer(ROOT_DIR, sys.executable, SERVICE_NAME)
                flash("Systemd auto-update timer enabled.", "success")
            elif auto_update.cron_supported():
                auto_update.enable_cron(ROOT_DIR, sys.executable, SERVICE_NAME)
                flash("Cron auto-update job installed.", "success")
            else:
                flash("No scheduler available (systemd or cron).", "error")
        elif action == "disable":
            if auto_update.systemd_supported():
                auto_update.disable_systemd_timer()
                flash("Systemd auto-update timer disabled.", "success")
            elif auto_update.cron_supported():
                auto_update.disable_cron()
                flash("Cron auto-update job removed.", "success")
            else:
                flash("No scheduler available to disable.", "error")
        else:
            flash("Unsupported auto-update action.", "error")
    except subprocess.CalledProcessError as exc:
        flash(exc.stderr or exc.stdout or str(exc), "error")
    except RuntimeError as exc:
        flash(str(exc), "error")
    except PermissionError:
        flash("Permission denied while configuring auto updates.", "error")
    return redirect(next_url)


@app.route("/restart", methods=["POST"])
def restart():
    return service_action("restart")


@app.route("/service/<action>", methods=["POST"])
def service_action(action: str):
    if action not in {"start", "stop", "restart", "status"}:
        flash("Invalid service action.", "error")
        return redirect(url_for("index"))
    try:
        result = subprocess.run(
            ["systemctl", action, SERVICE_NAME],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=True,
        )
        flash(result.stdout or f"Service {action} executed.", "success")
    except FileNotFoundError:
        flash("systemctl is not available on this system.", "error")
    except subprocess.CalledProcessError as exc:
        flash(exc.stdout or exc.stderr or f"Failed to {action} service.", "error")
    return redirect(url_for("index"))


@app.route("/service/validate-lavalink", methods=["POST"])
def validate_lavalink():
    """Validate Lavalink connectivity via ``elbotctl check``."""

    result = _run_elbotctl(["check"])
    if not result:
        return redirect(url_for("index"))

    output = (result.stdout or "").strip()
    if getattr(result, "returncode", 1) == 0:
        if output:
            flash(f"Lavalink validation succeeded:<pre>{output}</pre>", "success")
        else:
            flash("Lavalink validation succeeded.", "success")
    else:
        flash("Lavalink validation failed. See details above.", "error")
    return redirect(url_for("index"))


@app.route("/branch", methods=["GET", "POST"])
def branch():
    if request.method == "POST":
        branch_name = request.form.get("branch")
        if branch_name:
            subprocess.run(["git", "checkout", branch_name], cwd=ROOT_DIR, check=False)
        return redirect(url_for("branch"))

    current = _run("git", ["rev-parse", "--abbrev-ref", "HEAD"])
    branches = _run("git", ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    options = ""
    if branches:
        for b in branches.splitlines():
            selected = "selected" if b == current else ""
            options += f'<option value="{b}" {selected}>{b}</option>'
    return render_template(
        "branch.html",
        options=options
        or "<option disabled>Git is not available in this environment</option>",
    )


def _run(command: str, args: Iterable[str]) -> str:
    try:
        output = subprocess.check_output([command, *args], cwd=ROOT_DIR, text=True)
        return output.strip()
    except Exception:
        return ""


def _auto_update_worker() -> None:
    while True:
        try:
            _run_elbotctl(["update"])
            _run_elbotctl(["service", "restart"])
        except Exception as exc:  # pragma: no cover - background errors
            logging.getLogger("elbot.portal").error("Auto update failed: %s", exc)
        time.sleep(86400)


def main():
    if AUTO_UPDATE:
        threading.Thread(target=_auto_update_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
