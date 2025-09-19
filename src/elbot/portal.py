"""Web portal for installing and managing Elbot."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Iterable, Dict

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / '.env'
LOG_FILE = ROOT_DIR / 'logs' / 'elbot.log'
SERVICE_NAME = os.environ.get('ELBOT_SERVICE', 'elbot.service')
AUTO_UPDATE = os.environ.get('AUTO_UPDATE', '0') == '1'

REQUIRED_KEYS = ['DISCORD_TOKEN']
OPTIONAL_KEYS = ['OPENAI_API_KEY', 'LAVALINK_HOST', 'LAVALINK_PORT', 'LAVALINK_PASSWORD', 'AUTO_LAVALINK']

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('ELBOT_PORTAL_SECRET', 'change-me')


def _read_env(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.strip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key.strip()] = value.strip()
    return data


def _write_env(path: Path, values: Dict[str, str]) -> None:
    data = _read_env(path)
    data.update(values)
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _env_values() -> Dict[str, str]:
    return _read_env(ENV_FILE)


def _is_configured() -> bool:
    env = _env_values()
    return all(env.get(key) for key in REQUIRED_KEYS)


def _run_elbotctl(args: Iterable[str]) -> subprocess.CompletedProcess | None:
    cmd = [sys.executable, '-m', 'elbot.cli', *args]
    env = os.environ.copy()
    env.setdefault('PYTHONPATH', str(ROOT_DIR / 'src'))
    try:
        return subprocess.run(cmd, cwd=ROOT_DIR, text=True, capture_output=True, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        flash(exc.stdout or exc.stderr or str(exc), 'error')
        return exc
    except FileNotFoundError:
        flash('Python interpreter not found while invoking elbotctl.', 'error')
        return None


def _ensure_logs_dir() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


@app.context_processor
def inject_flags():
    return {'configured': _is_configured()}


@app.route('/')
def index():
    if not _is_configured():
        return redirect(url_for('setup'))
    return render_template('index.html')


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    values = _env_values()
    if request.method == 'POST':
        discord_token = request.form.get('discord_token', '').strip()
        openai_key = request.form.get('openai_api_key', '').strip()
        auto_lavalink = '1' if request.form.get('auto_lavalink') == 'on' else '0'
        lavalink_host = request.form.get('lavalink_host', '').strip() or 'localhost'
        lavalink_port = request.form.get('lavalink_port', '').strip() or '2333'
        lavalink_password = request.form.get('lavalink_password', '').strip() or 'youshallnotpass'

        if not discord_token:
            flash('Discord token is required.', 'error')
        else:
            updates = {
                'DISCORD_TOKEN': discord_token,
                'OPENAI_API_KEY': openai_key,
                'AUTO_LAVALINK': auto_lavalink,
            }
            if auto_lavalink == '0':
                updates.update({
                    'LAVALINK_HOST': lavalink_host,
                    'LAVALINK_PORT': lavalink_port,
                    'LAVALINK_PASSWORD': lavalink_password,
                })
            _write_env(ENV_FILE, updates)
            flash('Configuration saved. Installing dependencies…', 'info')
            result = _run_elbotctl(['install', '--non-interactive', '--no-service'])
            if result and getattr(result, 'returncode', 0) == 0:
                flash(result.stdout or 'Installation complete.', 'success')
                return redirect(url_for('index'))
            else:
                flash(getattr(result, 'stdout', '') or 'Installation failed.', 'error')
        values = _env_values()
    return render_template('setup.html', values=values)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    values = _env_values()
    if request.method == 'POST':
        updates = {}
        for key in REQUIRED_KEYS + OPTIONAL_KEYS:
            if key in request.form:
                updates[key] = request.form.get(key, '').strip()
        _write_env(ENV_FILE, updates)
        flash('Settings updated.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', values=values)


@app.route('/logs')
def view_logs():
    _ensure_logs_dir()
    logs = ''
    if LOG_FILE.exists():
        logs = ''.join(LOG_FILE.read_text(encoding='utf-8').splitlines(True)[-200:])
    return render_template('logs.html', logs=logs)


@app.route('/update', methods=['POST'])
def update():
    result = _run_elbotctl(['update'])
    if result and getattr(result, 'returncode', 0) == 0:
        flash(result.stdout or 'Update completed.', 'success')
    return redirect(url_for('index'))


@app.route('/service/<action>', methods=['POST'])
def service_action(action: str):
    if action not in {'start', 'stop', 'restart', 'status'}:
        flash('Invalid service action.', 'error')
        return redirect(url_for('index'))
    result = _run_elbotctl(['service', action])
    if result and getattr(result, 'returncode', 0) == 0:
        flash(result.stdout or f'Service {action} executed.', 'success')
    return redirect(url_for('index'))


@app.route('/service/validate-lavalink', methods=['POST'])
def validate_lavalink():
    """Validate Lavalink connectivity via ``elbotctl check``."""

    result = _run_elbotctl(['check'])
    if not result:
        return redirect(url_for('index'))

    output = (result.stdout or '').strip()
    if getattr(result, 'returncode', 1) == 0:
        if output:
            flash(f'Lavalink validation succeeded:<pre>{output}</pre>', 'success')
        else:
            flash('Lavalink validation succeeded.', 'success')
    else:
        flash('Lavalink validation failed. See details above.', 'error')
    return redirect(url_for('index'))


@app.route('/branch', methods=['GET', 'POST'])
def branch():
    if request.method == 'POST':
        branch_name = request.form.get('branch')
        if branch_name:
            subprocess.run(['git', 'checkout', branch_name], cwd=ROOT_DIR, check=False)
        return redirect(url_for('branch'))

    current = _run('git', ['rev-parse', '--abbrev-ref', 'HEAD'])
    branches = _run('git', ['for-each-ref', '--format=%(refname:short)', 'refs/heads'])
    options = ''
    if branches:
        for b in branches.splitlines():
            selected = 'selected' if b == current else ''
            options += f'<option value="{b}" {selected}>{b}</option>'
    return render_template('branch.html', options=options or '<option disabled>No git available</option>')


def _run(command: str, args: Iterable[str]) -> str:
    try:
        output = subprocess.check_output([command, *args], cwd=ROOT_DIR, text=True)
        return output.strip()
    except Exception:
        return ''


def _auto_update_worker() -> None:
    while True:
        try:
            _run_elbotctl(['update'])
            _run_elbotctl(['service', 'restart'])
        except Exception as exc:  # pragma: no cover - background errors
            logging.getLogger('elbot.portal').error('Auto update failed: %s', exc)
        time.sleep(86400)


def main():
    if AUTO_UPDATE:
        threading.Thread(target=_auto_update_worker, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))


if __name__ == '__main__':
    main()
