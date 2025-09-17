import importlib
from pathlib import Path
import subprocess


from elbot import portal


def make_client(monkeypatch, *, check_output=None, run=None):
    importlib.reload(portal)
    monkeypatch.setattr(portal, "LOG_FILE", Path(__file__))
    monkeypatch.setattr(portal, "AUTO_UPDATE", False)
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
