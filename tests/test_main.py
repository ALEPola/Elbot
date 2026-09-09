import argparse
import asyncio

import pytest

import elbot.config as config_module
from elbot import cli, main


class _FakeResponse:
    def __init__(self, status: int, payload: object):
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # pragma: no cover - compatibility
        return False

    async def json(self, content_type=None):  # pragma: no cover - signature compat
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = iter(responses)

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # pragma: no cover - compatibility
        return False

    def get(self, url, **kwargs):  # pragma: no cover - signature compat
        try:
            return next(self._responses)
        except StopIteration:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected request to {url}")


def _install_fake_session(monkeypatch):
    def _factory(*_args, **_kwargs):
        return _FakeSession(
            [
                _FakeResponse(200, {"plugins": []}),
                _FakeResponse(200, {"tracks": []}),
                _FakeResponse(200, {"tracks": []}),
            ]
        )

    monkeypatch.setattr(main.aiohttp, "ClientSession", _factory)


def test_command_check_reports_loadtracks_failure(monkeypatch):
    monkeypatch.setattr(main.Config, "LAVALINK_HOST", "localhost", raising=False)
    monkeypatch.setattr(main.Config, "LAVALINK_PORT", 2333, raising=False)
    monkeypatch.setattr(main.Config, "LAVALINK_PASSWORD", "password", raising=False)

    monkeypatch.setattr(
        config_module.Config,
        "validate",
        staticmethod(lambda: None),
        raising=False,
    )
    monkeypatch.setattr(
        config_module,
        "log_cookie_status",
        lambda: None,
        raising=False,
    )

    _install_fake_session(monkeypatch)

    with pytest.raises(cli.CommandError) as excinfo:
        cli.command_check(argparse.Namespace())

    message = str(excinfo.value)
    assert "Lavalink health check failed" in message
    assert "loadtracks" in message.lower()


def test_lavalink_health_check_returns_failure(monkeypatch):
    monkeypatch.setattr(main.Config, "LAVALINK_HOST", "localhost", raising=False)
    monkeypatch.setattr(main.Config, "LAVALINK_PORT", 2333, raising=False)
    monkeypatch.setattr(main.Config, "LAVALINK_PASSWORD", "password", raising=False)

    _install_fake_session(monkeypatch)

    success, reason = asyncio.run(main._lavalink_health_check())

    assert not success
    assert reason == "/loadtracks returned no tracks"


def test_fetch_lavalink_plugins_reads_v4_info_shape():
    version = asyncio.run(
        main._fetch_lavalink_plugins(
            {"plugins": [{"name": "youtube-plugin", "version": "1.18.2"}]}
        )
    )

    assert version == "1.18.2"


def test_command_error_logs_original_exception_and_reference(caplog):
    try:
        raise ValueError("test failure")
    except ValueError as error:
        wrapped = main.commands.CommandInvokeError(error)
        reference = main._log_command_error(wrapped, "play")
    assert len(reference) == 12
    assert reference in caplog.text
    assert "command=play" in caplog.text
    assert "ValueError: test failure" in caplog.text
    assert caplog.records[-1].exc_info[0] is ValueError


def test_validation_resolves_live_port(monkeypatch, tmp_path):
    from elbot.runtime_health import publish_health
    monkeypatch.setattr(config_module.Config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main.Config, "LAVALINK_PORT", 0)
    publish_health(tmp_path / "logs" / "health.json", discord_ready=True, music_ready=False, lavalink_port=2341)
    assert main.resolve_lavalink_port(0) == 2341


def test_validation_rejects_stale_port(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(config_module.Config, "BASE_DIR", tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "health.json").write_text(json.dumps({"updated_at": 1, "lavalink_port": 2333}))
    with pytest.raises(ValueError, match="runtime port"):
        main.resolve_lavalink_port(0)
