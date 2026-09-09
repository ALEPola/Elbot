"""Web portal for installing and managing Elbot."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from flask import (
    Flask,
    has_request_context,
    session,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from werkzeug.security import check_password_hash
from markupsafe import escape

from .runtime_health import read_health
from .file_io import atomic_write_text
from .core import auto_update
from .config import Config
from .music import CookieManager, DiagnosticsReport, DiagnosticsService, PlaybackMetrics

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
SENSITIVE_KEYS = {
    "DISCORD_TOKEN",
    "OPENAI_API_KEY",
    "LAVALINK_PASSWORD",
    "AUTO_UPDATE_WEBHOOK",
}

logger = logging.getLogger("elbot.portal")

def _portal_secret_key() -> str:
    configured = os.environ.get("ELBOT_PORTAL_SECRET", "").strip()
    if configured:
        return configured
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "ELBOT_PORTAL_SECRET is not set; using an ephemeral session secret. "
        "Set ELBOT_PORTAL_SECRET in the environment for persistent sessions."
    )
    return generated


app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = _portal_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("ELBOT_PORTAL_HTTPS", "0") == "1",
    MAX_CONTENT_LENGTH=64 * 1024,
)


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect_portal():
    password_hash = os.environ.get("ELBOT_PORTAL_PASSWORD_HASH", "").strip()
    if not password_hash:
        return "Panel access is disabled. Configure ELBOT_PORTAL_PASSWORD_HASH on the host.", 503
    credentials = request.authorization
    username = os.environ.get("ELBOT_PORTAL_USERNAME", "admin")
    try:
        authenticated = (
            credentials and credentials.type == "basic"
            and secrets.compare_digest((credentials.username or "").encode(), username.encode())
            and check_password_hash(password_hash, credentials.password or "")
        )
    except ValueError:
        logger.error("Invalid panel password hash configuration")
        return "Panel credentials are misconfigured. Check the host configuration.", 503
    if not authenticated:
        return "Authentication required.", 401, {"WWW-Authenticate": 'Basic realm="ELBOT", charset="UTF-8"'}
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
            return jsonify(error="Invalid or missing CSRF token. Reload the page and try again."), 403


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.errorhandler(subprocess.TimeoutExpired)
def command_timeout(error):
    logger.warning("Panel operation timed out after %ss", error.timeout)
    return jsonify(error="Operation timed out. Check service status before retrying."), 504


@app.errorhandler(subprocess.CalledProcessError)
def command_failed(error):
    logger.error("Panel operation failed with exit code %s", error.returncode)
    return jsonify(error="Operation failed. Check the service logs for details."), 502


def _report_error(message: str) -> None:
    logger.error("%s", message)
    if has_request_context():
        flash(message, "error")


_DIAGNOSTICS_COOKIES = CookieManager()
_DIAGNOSTICS_METRICS = PlaybackMetrics()


def _read_env(path: Path) -> Dict[str, str]:
    from dotenv import dotenv_values

    if not path.exists():
        return {}
    return {key: value or "" for key, value in dotenv_values(path, interpolate=False).items()}


def _write_env(path: Path, values: Dict[str, str]) -> None:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or any(c in value for c in "\r\n\0"):
            raise ValueError("Configuration values must be single-line text.")
    if "LAVALINK_PORT" in values:
        port = values["LAVALINK_PORT"]
        if not port.isdigit() or not 0 <= int(port) <= 65535:
            raise ValueError("LAVALINK_PORT must be between 0 and 65535.")
    # Preserve comments and unrelated settings, and quote literal values for dotenv.
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    output = []
    for line in lines:
        key = line.partition("=")[0].strip().removeprefix("export ").strip()
        if key in values:
            if key in remaining:
                value = remaining.pop(key).replace("\\", "\\\\").replace("'", "\\'")
                output.append(f"{key}='{value}'")
        else:
            output.append(line)
    for key, value in remaining.items():
        value = value.replace("\\", "\\\\").replace("'", "\\'")
        output.append(f"{key}='{value}'")
    atomic_write_text(path, "\n".join(output) + "\n")


def _env_values() -> Dict[str, str]:
    return _read_env(ENV_FILE)


def _public_env_values(values: Dict[str, str] | None = None) -> Dict[str, str]:
    source = dict(values if values is not None else _env_values())
    for key in SENSITIVE_KEYS:
        if key in source:
            source[key] = ""
    return source


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

    async def _run_collect() -> DiagnosticsReport:
        try:
            return await service.collect()
        finally:
            await service.close()

    try:
        report = asyncio.run(_run_collect())
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
            cmd, cwd=ROOT_DIR, text=True, capture_output=True, env=env, check=True,
            timeout=600 if any(arg in {"update", "install"} for arg in cmd[3:]) else 60
        )
    except subprocess.TimeoutExpired:
        _report_error("Operation timed out. Check service status before retrying.")
        return None
    except subprocess.CalledProcessError as exc:
        _report_error(exc.stderr or exc.stdout or str(exc))
        return exc
    except FileNotFoundError:
        _report_error("Python interpreter not found while invoking elbotctl.")
        return None


def _ensure_logs_dir() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    return "".join(path.read_text(encoding="utf-8").splitlines(True)[-max_lines:])


def _scheduler_status():
    try:
        return auto_update.current_status()
    except (subprocess.TimeoutExpired, OSError):
        return auto_update.AutoUpdateStatus(
            mode="systemd", details=auto_update.SystemdTimerStatus(supported=True, error="Scheduler status unavailable.")
        )


@app.context_processor
def inject_flags():
    return {
        "configured": _is_configured(),
        "auto_update_status": _scheduler_status(),
        "legacy_auto_update": AUTO_UPDATE,
        "auto_update": AUTO_UPDATE,
        "auto_lavalink_enabled": _auto_lavalink_enabled(),
    }


@app.route("/")
def index():
    if not _is_configured() and not app.config.get("TESTING"):
        return redirect(url_for("setup"))
    return render_template("index.html", health=read_health(ROOT_DIR / "logs" / "health.json"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    values = _public_env_values()
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
            try:
                _write_env(ENV_FILE, updates)
            except (ValueError, OSError):
                flash("Configuration could not be saved. Check values and file permissions.", "error")
                return render_template("setup.html", values=values), 400
            flash("Configuration saved. Installing dependencies...", "info")
            result = _run_elbotctl(["install", "--non-interactive", "--no-service"])
            if result and getattr(result, "returncode", 0) == 0:
                flash(result.stdout or "Installation complete.", "success")
                return redirect(url_for("index"))
            else:
                flash(getattr(result, "stdout", "") or "Installation failed.", "error")
        values = _public_env_values(_env_values())
    return render_template("setup.html", values=values)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    values = _public_env_values()
    if request.method == "POST":
        current_values = _env_values()
        updates = {}
        for key in REQUIRED_KEYS + OPTIONAL_KEYS:
            if key in request.form:
                value = request.form.get(key, "").strip()
                if key in SENSITIVE_KEYS and not value:
                    value = current_values.get(key, "")
                updates[key] = value
        try:
            _write_env(ENV_FILE, updates)
        except (ValueError, OSError):
            flash("Configuration could not be saved. Check values and file permissions.", "error")
            return render_template("settings.html", values=values), 400
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


@app.route("/api/health")
def api_health():
    health = read_health(ROOT_DIR / "logs" / "health.json")
    return jsonify(health), 200 if health["status"] == "ready" else 503


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

    from openai import OpenAI

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
            timeout=30,
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
            timeout=30,
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
    next_url = url_for("index")
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
        _report_error(exc.stderr or exc.stdout or str(exc))
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
            timeout=60,
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
            flash(f"Lavalink validation succeeded: {output}", "success")
        else:
            flash("Lavalink validation succeeded.", "success")
    else:
        flash("Lavalink validation failed. See details above.", "error")
    return redirect(url_for("index"))


@app.route("/branch", methods=["GET", "POST"])
def branch():
    if request.method == "POST":
        branch_name = request.form.get("branch")
        known = _run("git", ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
        known_branches = known.splitlines() if known else []
        if branch_name and branch_name in known_branches:
            subprocess.run(["git", "checkout", branch_name], cwd=ROOT_DIR, check=True, timeout=30)
        return redirect(url_for("branch"))

    current = _run("git", ["rev-parse", "--abbrev-ref", "HEAD"])
    branches = _run("git", ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    options = ""
    if branches:
        for b in branches.splitlines():
            selected = "selected" if b == current else ""
            options += f'<option value="{escape(b)}" {selected}>{escape(b)}</option>'
    return render_template(
        "branch.html",
        options=options
        or "<option disabled>Git is not available in this environment</option>",
    )


def _run(command: str, args: Iterable[str]) -> str:
    try:
        output = subprocess.check_output([command, *args], cwd=ROOT_DIR, text=True, timeout=30)
        return output.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _auto_update_once() -> None:
    result = _run_elbotctl(["update"])
    if result is None or result.returncode != 0:
        logger.error("Automatic update failed; restart skipped.")
        return
    result = _run_elbotctl(["service", "restart"])
    if result is None or result.returncode != 0:
        logger.error("Automatic update succeeded but service restart failed.")
    else:
        logger.info("Automatic update and restart succeeded.")


def _auto_update_worker() -> None:
    while True:
        try:
            _auto_update_once()
        except Exception as exc:  # pragma: no cover - background errors
            logging.getLogger("elbot.portal").error("Auto update failed: %s", exc)
        time.sleep(86400)


def main():
    if AUTO_UPDATE:
        threading.Thread(target=_auto_update_worker, daemon=True).start()
    app.run(host=os.environ.get("ELBOT_PORTAL_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))


if __name__ == "__main__":
    main()
