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
