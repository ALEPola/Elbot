import importlib
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace


from elbot import config as elbot_config
from elbot import portal


def make_client(monkeypatch, *, check_output=None, run=None, root_dir=None, env_file=None):
    importlib.reload(portal)
    if root_dir is None:
        root_dir = Path(tempfile.mkdtemp(prefix="portal-root-"))
    if env_file is None:
        env_file = root_dir / ".env"

    monkeypatch.setattr(portal, "ROOT_DIR", root_dir)
    monkeypatch.setattr(portal, "ENV_FILE", env_file)
    monkeypatch.setattr(portal, "LOG_FILE", Path(__file__))
    monkeypatch.setattr(portal, "UPDATE_LOG_FILE", Path(__file__))
    monkeypatch.setattr(portal, "AUTO_UPDATE_LOG_FILE", Path(__file__))
    monkeypatch.setattr(portal, "AUTO_UPDATE", False)

    status = SimpleNamespace(mode='disabled', details=None, cron_enabled=False)
    monkeypatch.setattr(portal.auto_update, 'current_status', lambda: status)
    monkeypatch.setattr(portal.auto_update, 'systemd_supported', lambda: False)
    monkeypatch.setattr(portal.auto_update, 'cron_supported', lambda: False)

    if check_output is None:
        monkeypatch.setattr(
            subprocess,
            "check_output",
            lambda *a, **k: b"main\n" if b"rev-parse" in a[0] else b"main\n",
        )
    else:
        monkeypatch.setattr(subprocess, "check_output", check_output)
    if run is not None:
        monkeypatch.setattr(subprocess, "run", run)
    portal.app.config["TESTING"] = True
    return portal.app.test_client()


def test_portal_secret_uses_env(monkeypatch):
    monkeypatch.setenv("ELBOT_PORTAL_SECRET", "configured-secret")
    importlib.reload(portal)
    assert portal.app.secret_key == "configured-secret"


def test_portal_secret_fallback_is_not_static(monkeypatch):
    monkeypatch.delenv("ELBOT_PORTAL_SECRET", raising=False)
    importlib.reload(portal)
    key = portal.app.secret_key
    assert isinstance(key, str)
    assert key
    assert key != "change-me"



def test_is_configured_uses_project_root(monkeypatch, tmp_path):
    monkeypatch.setattr(elbot_config.Config, "BASE_DIR", tmp_path)
    importlib.reload(portal)

    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_TOKEN=abc\n", encoding="utf-8")

    assert portal.ROOT_DIR == tmp_path
    assert portal.ENV_FILE == env_path
    assert portal._is_configured() is True


def test_index(monkeypatch):
    client = make_client(monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Elbot Portal" in resp.data


def test_logs(monkeypatch):
    client = make_client(monkeypatch)
    resp = client.get("/logs")
    assert resp.status_code == 200


def test_logs_summary_requires_api_key(monkeypatch):
    client = make_client(monkeypatch)
    resp = client.post('/logs/summary')
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'OpenAI API key is not configured.'


def test_logs_summary_success(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = make_client(monkeypatch)

    def fake_summary(text, *, api_key):
        assert api_key == 'test-key'
        assert 'make_client' in text
        return 'All clear'

    monkeypatch.setattr(portal, '_summarize_logs_with_ai', fake_summary)
    resp = client.post('/logs/summary')
    assert resp.status_code == 200
    assert resp.get_json()['summary'] == 'All clear'


def test_branch_get(monkeypatch):
    client = make_client(monkeypatch)
    resp = client.get("/branch")
    assert resp.status_code == 200
    assert b"Switch Branch" in resp.data


def test_branch_git_missing(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    client = make_client(monkeypatch, check_output=missing)
    resp = client.get("/branch")
    assert resp.status_code == 200
    assert b"Git is not available in this environment" in resp.data


def test_update_and_restart(monkeypatch):
    ran = []

    def fake_run(*args, **kwargs):
        ran.append(args[0])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = make_client(monkeypatch)
    resp = client.post("/update")
    assert resp.status_code == 302
    resp = client.post("/restart")
    assert resp.status_code == 302
    assert any("systemctl" in cmd[0] for cmd in ran)


def test_update_status(monkeypatch):
    ran = []

    def fake_run(*args, **kwargs):
        ran.append(args[0])
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"up to date")
    client = make_client(monkeypatch)
    resp = client.get("/update_status")
    assert resp.status_code == 200
    assert ran


def test_update_status_git_missing(monkeypatch):
    def missing_run(*args, **kwargs):
        raise FileNotFoundError("git")

    client = make_client(monkeypatch, run=missing_run)
    resp = client.get("/update_status")
    assert resp.status_code == 200
    assert b"Git is not available in this environment" in resp.data


def test_restart_missing_systemctl(monkeypatch):
    def missing_run(*args, **kwargs):
        raise FileNotFoundError("systemctl")

    client = make_client(monkeypatch, run=missing_run)
    resp = client.post("/restart")
    assert resp.status_code == 302


def test_auto_update_enable_systemd(monkeypatch):
    client = make_client(monkeypatch)
    called = {}
    status = SimpleNamespace(mode='systemd', details=SimpleNamespace(enabled=False, next_run=None, last_trigger=None, error=None), cron_enabled=False)
    monkeypatch.setattr(portal.auto_update, 'systemd_supported', lambda: True)
    monkeypatch.setattr(portal.auto_update, 'enable_systemd_timer', lambda *args: called.setdefault('enable', args))
    monkeypatch.setattr(portal.auto_update, 'current_status', lambda: status)

    resp = client.post('/auto-update', data={'action': 'enable'})
    assert resp.status_code == 302
    assert 'enable' in called


def test_auto_update_disable_systemd(monkeypatch):
    client = make_client(monkeypatch)
    called = {}
    status = SimpleNamespace(mode='systemd', details=SimpleNamespace(enabled=True, next_run=None, last_trigger=None, error=None), cron_enabled=False)
    monkeypatch.setattr(portal.auto_update, 'systemd_supported', lambda: True)
    monkeypatch.setattr(portal.auto_update, 'disable_systemd_timer', lambda: called.setdefault('disable', True))
    monkeypatch.setattr(portal.auto_update, 'current_status', lambda: status)

    resp = client.post('/auto-update', data={'action': 'disable'})
    assert resp.status_code == 302
    assert called.get('disable') is True


def test_auto_update_enable_cron(monkeypatch):
    client = make_client(monkeypatch)
    called = {}
    status = SimpleNamespace(mode='cron', details=None, cron_enabled=False)
    monkeypatch.setattr(portal.auto_update, 'systemd_supported', lambda: False)
    monkeypatch.setattr(portal.auto_update, 'cron_supported', lambda: True)
    monkeypatch.setattr(portal.auto_update, 'enable_cron', lambda *args: called.setdefault('enable', args))
    monkeypatch.setattr(portal.auto_update, 'current_status', lambda: status)

    resp = client.post('/auto-update', data={'action': 'enable'})
    assert resp.status_code == 302
    assert 'enable' in called


def test_setup_hides_sensitive_values(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=secret\nOPENAI_API_KEY=sk-test\nLAVALINK_PASSWORD=pw\n",
        encoding="utf-8",
    )
    client = make_client(monkeypatch, root_dir=tmp_path, env_file=env_path)

    resp = client.get("/setup")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert 'type="password" name="discord_token"' in body
    assert "secret" not in body
    assert "sk-test" not in body
    assert "pw" not in body


def test_settings_preserves_secrets_when_blank(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=secret\nOPENAI_API_KEY=sk-test\nLAVALINK_PASSWORD=pw\n",
        encoding="utf-8",
    )
    client = make_client(monkeypatch, root_dir=tmp_path, env_file=env_path)

    resp = client.post(
        "/settings",
        data={
            "DISCORD_TOKEN": "",
            "OPENAI_API_KEY": "",
            "AUTO_UPDATE_WEBHOOK": "",
            "AUTO_LAVALINK": "1",
            "LAVALINK_HOST": "localhost",
            "LAVALINK_PORT": "2333",
            "LAVALINK_PASSWORD": "",
        },
    )
    assert resp.status_code == 302

    updated = env_path.read_text(encoding="utf-8")
    assert "DISCORD_TOKEN=secret" in updated
    assert "OPENAI_API_KEY=sk-test" in updated
    assert "LAVALINK_PASSWORD=pw" in updated
