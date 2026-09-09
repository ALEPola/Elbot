import base64
import pytest
from werkzeug.security import generate_password_hash

import importlib
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace


from elbot import config as elbot_config
from elbot import portal


TEST_HASH = generate_password_hash("test-password", method="pbkdf2:sha256:1000")


def make_client(monkeypatch, *, check_output=None, run=None, root_dir=None, env_file=None):
    monkeypatch.setenv("ELBOT_PORTAL_PASSWORD_HASH", TEST_HASH)
    monkeypatch.setenv("ELBOT_PORTAL_USERNAME", "admin")
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
    client = portal.app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"admin:test-password").decode()
    with client.session_transaction() as session:
        session["csrf_token"] = "test-csrf"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "test-csrf"
    return client


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

    updated = portal._read_env(env_path)
    assert updated["DISCORD_TOKEN"] == "secret"
    assert updated["OPENAI_API_KEY"] == "sk-test"
    assert updated["LAVALINK_PASSWORD"] == "pw"


@pytest.mark.parametrize("path", ["/", "/logs", "/settings", "/api/health", "/setup", "/static/style.css"])
def test_auth_required(monkeypatch, path):
    client = make_client(monkeypatch)
    client.environ_base.pop("HTTP_AUTHORIZATION")
    assert client.get(path).status_code == 401


def test_auth_disabled_without_hash(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.delenv("ELBOT_PORTAL_PASSWORD_HASH")
    assert client.get("/").status_code == 503


def test_wrong_credentials(monkeypatch):
    client = make_client(monkeypatch)
    assert client.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}).status_code == 401


@pytest.mark.parametrize("path", ["/setup", "/settings", "/restart", "/update", "/auto-update", "/branch", "/logs/summary", "/service/stop", "/service/validate-lavalink"])
def test_csrf_blocks_mutations(monkeypatch, path):
    def unexpected(*args, **kwargs):
        pytest.fail("Unauthenticated mutation reached a subprocess")
    client = make_client(monkeypatch, run=unexpected)
    client.environ_base.pop("HTTP_X_CSRF_TOKEN")
    assert client.post(path).status_code == 403
    assert client.post(path, data={"csrf_token": "wrong"}).status_code == 403


def test_forms_and_csrf_submission(monkeypatch):
    client = make_client(monkeypatch, run=lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    client.environ_base.pop("HTTP_X_CSRF_TOKEN")
    body = client.get("/").get_data(as_text=True)
    assert body.count('name="csrf_token"') == body.count("<form")
    assert client.post("/restart", data={"csrf_token": "test-csrf"}).status_code == 302


def test_timeout_response(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args[0], 60)
    client = make_client(monkeypatch, run=timeout)
    response = client.post("/restart")
    assert response.status_code == 504
    assert "timed out" in response.json["error"]


def test_background_update_failure_skips_restart(monkeypatch, caplog):
    calls = []
    def failed(*args, **kwargs):
        calls.append(args[0])
        raise subprocess.CalledProcessError(1, args[0], stderr="update failed")
    monkeypatch.setattr(subprocess, "run", failed)
    portal._auto_update_once()
    assert len(calls) == 1
    assert "restart skipped" in caplog.text


def test_background_update_success_restarts(monkeypatch):
    calls = []
    def success(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(portal, "_run_elbotctl", success)
    portal._auto_update_once()
    assert calls == [["update"], ["service", "restart"]]


def test_background_timeout_skips_restart(monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args[0])
        assert kwargs["timeout"] == 600
        raise subprocess.TimeoutExpired(args[0], 600)
    monkeypatch.setattr(subprocess, "run", timeout)
    portal._auto_update_once()
    assert len(calls) == 1


@pytest.mark.parametrize("host", [None, "0.0.0.0"])
def test_bind_address(monkeypatch, host):
    monkeypatch.setattr(portal, "AUTO_UPDATE", False)
    monkeypatch.delenv("ELBOT_PORTAL_HOST", raising=False)
    if host:
        monkeypatch.setenv("ELBOT_PORTAL_HOST", host)
    calls = []
    monkeypatch.setattr(portal.app, "run", lambda **kwargs: calls.append(kwargs))
    portal.main()
    assert calls[0]["host"] == (host or "127.0.0.1")


def test_config_failed_replace_preserves_original(monkeypatch, tmp_path):
    from elbot import file_io
    path = tmp_path / ".env"
    original = "# comment\nDISCORD_TOKEN=old\n"
    path.write_text(original)
    def failure(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(file_io.os, "replace", failure)
    with pytest.raises(OSError):
        portal._write_env(path, {"DISCORD_TOKEN": "new"})
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_config_round_trip_and_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# retain me\nUNRELATED=value\n")
    value = "spaces # quotes ' and backslash " + chr(92)
    portal._write_env(path, {"DISCORD_TOKEN": value})
    assert portal._read_env(path)["DISCORD_TOKEN"] == value
    assert "# retain me" in path.read_text()
    assert portal._read_env(path)["UNRELATED"] == "value"


@pytest.mark.parametrize("values", [{"DISCORD_TOKEN": "x\nINJECTED=yes"}, {"LAVALINK_PORT": "99999"}])
def test_invalid_config_preserves_file(tmp_path, values):
    path = tmp_path / ".env"
    path.write_text("DISCORD_TOKEN=original\n")
    with pytest.raises(ValueError):
        portal._write_env(path, values)
    assert path.read_text() == "DISCORD_TOKEN=original\n"


def test_health_endpoint(monkeypatch, tmp_path):
    from elbot.runtime_health import publish_health
    client = make_client(monkeypatch, root_dir=tmp_path)
    assert client.get("/api/health").status_code == 503
    publish_health(tmp_path / "logs" / "health.json", discord_ready=True, music_ready=True)
    assert client.get("/api/health").json["status"] == "ready"
    assert client.get("/api/health").status_code == 200


def test_browser_receives_usable_csrf_token(monkeypatch):
    client = make_client(monkeypatch, run=lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    client.environ_base.pop("HTTP_X_CSRF_TOKEN")
    with client.session_transaction() as session:
        session.clear()
    assert client.get("/").status_code == 200
    with client.session_transaction() as session:
        token = session["csrf_token"]
    assert client.post("/restart", data={"csrf_token": token}).status_code == 302


def test_cli_reads_panel_quoted_settings(tmp_path):
    from elbot.core import ops
    path = tmp_path / ".env"
    password = "a'b" + chr(92) + "c"
    portal._write_env(path, {"LAVALINK_PASSWORD": password})
    assert ops.read_env(path)["LAVALINK_PASSWORD"] == password


def test_diagnostics_resolves_live_port(monkeypatch, tmp_path):
    from elbot.runtime_health import publish_health
    monkeypatch.setattr(portal.Config, "BASE_DIR", tmp_path)
    publish_health(tmp_path / "logs" / "health.json", discord_ready=True, music_ready=False, lavalink_port=2340)
    service, meta = portal._diagnostics_service({"LAVALINK_PORT": "0"})
    assert meta["port"] == 2340


def test_diagnostics_missing_runtime_port(monkeypatch, tmp_path):
    monkeypatch.setattr(portal.Config, "BASE_DIR", tmp_path)
    with pytest.raises(ValueError, match="runtime port is unavailable"):
        portal._diagnostics_service({"LAVALINK_PORT": "0"})
