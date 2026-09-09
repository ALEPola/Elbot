import json

import pytest

from elbot.runtime_health import publish_health, read_health


@pytest.mark.parametrize("discord,music,status", [
    (True, True, "ready"), (True, False, "degraded"), (False, False, "disconnected"),
])
def test_health_states(tmp_path, discord, music, status):
    path = tmp_path / "health.json"
    publish_health(path, discord_ready=discord, music_ready=music)
    assert read_health(path)["status"] == status


def test_stale_health_does_not_report_old_connection(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"updated_at": 100, "discord_ready": True, "music_ready": True}))
    result = read_health(path, now=146)
    assert result["status"] == "stale"
    assert result["discord"] == "unknown"


@pytest.mark.parametrize("content", ["broken", "[]", "null", '{}', '{"updated_at": "NaN"}'])
def test_invalid_health_is_unknown(tmp_path, content):
    path = tmp_path / "health.json"
    path.write_text(content)
    assert read_health(path)["status"] == "unknown"
