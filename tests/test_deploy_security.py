import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux deployment script")
SCRIPT = Path(__file__).resolve().parents[1] / "infra/scripts/deploy.sh"


def run_deploy(tmp_path, **overrides):
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "calls"
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    for name in ["ssh", "rsync"]:
        tool = tools / name
        tool.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$CALL_LOG"\nexit "${STUB_EXIT:-0}"\n')
        tool.chmod(0o755)
    env = {**os.environ, "PATH": str(tools) + ":" + os.environ["PATH"], "HOME": str(tmp_path),
           "TMPDIR": str(temporary), "DEPLOY_HOST": "example.test", "DEPLOY_USER": "deploy",
           "DEPLOY_PATH": "/opt/elbot", "DEPLOY_KNOWN_HOSTS": "example.test ssh-ed25519 test-only",
           "CALL_LOG": str(log), "DEPLOY_INCLUDE_ENV": "0", **overrides}
    result = subprocess.run(["bash", str(SCRIPT)], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=10)
    return result, log.read_text() if log.exists() else "", temporary


def test_deployment_requires_verified_host_keys_and_preserves_secrets(tmp_path):
    result, calls, temporary = run_deploy(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "StrictHostKeyChecking=yes" in calls
    assert "StrictHostKeyChecking=no" not in calls
    for protected in [".env", ".env.*", "data", "*cookies*.txt"]:
        assert protected in calls
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("overrides", [
    {"DEPLOY_KNOWN_HOSTS": ""}, {"DEPLOY_INCLUDE_ENV": "1"},
    {"DEPLOY_PATH": "/"}, {"DEPLOY_PATH": "/opt/../elbot"},
    {"DEPLOY_PATH": "/opt/elbot'; touch injected; '"},
    {"DEPLOY_COMPOSE_FILE": "../../other.yml"},
])
def test_deployment_rejects_unsafe_configuration_before_remote_actions(tmp_path, overrides):
    result, calls, _ = run_deploy(tmp_path, **overrides)
    assert result.returncode != 0
    assert calls == ""


def test_deployment_cleans_temporary_key_on_ssh_failure(tmp_path):
    result, _, temporary = run_deploy(tmp_path, SSH_PRIVATE_KEY="test-only", STUB_EXIT="1")
    assert result.returncode != 0
    assert not list(temporary.iterdir())
