import importlib
from pathlib import Path
from types import SimpleNamespace
import subprocess


from elbot import portal


def make_client(monkeypatch, *, check_output=None, run=None):
    importlib.reload(portal)
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
