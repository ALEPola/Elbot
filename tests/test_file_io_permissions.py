import os
import stat

import pytest

from elbot.file_io import atomic_write_text


@pytest.mark.skipif(os.name == "nt", reason="POSIX service permissions")
def test_atomic_replacement_preserves_service_access_without_world_read(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text("old")
    path.chmod(0o660)
    atomic_write_text(path, "new")
    assert path.read_text() == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o660


@pytest.mark.skipif(os.name == "nt", reason="POSIX service permissions")
def test_new_atomic_file_is_readable_by_service_group_only(tmp_path):
    path = tmp_path / "runtime.json"
    atomic_write_text(path, "new")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX service permissions")
def test_shared_venv_install_repairs_private_metadata_without_world_read(tmp_path, monkeypatch):
    from elbot import cli

    venv = tmp_path / "venv"
    metadata = venv / "package.dist-info"
    metadata.mkdir(parents=True)
    metadata.chmod(0o700)
    record = metadata / "METADATA"
    record.write_text("fixture")
    record.chmod(0o600)
    monkeypatch.setattr(cli, "VENV_DIR", venv)
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setenv("ELBOT_SHARED_RUNTIME", "1")
    monkeypatch.setattr(cli.ops, "pip_install", lambda *args, **kwargs: None)
    cli._pip_install(["install", "fixture"])
    assert stat.S_IMODE(record.stat().st_mode) == 0o640
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o2750
