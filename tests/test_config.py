import importlib

import logging
import pytest


def test_config_validate_missing_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import elbot.config as config

    importlib.reload(config)
    with pytest.raises(RuntimeError):
        config.Config.validate()


def test_config_validate_pass(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    import elbot.config as config

    importlib.reload(config)
    config.Config.validate()


def test_invalid_guild_id_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("GUILD_ID", "abc")

    import elbot.config as config

    with caplog.at_level(logging.WARNING):
        importlib.reload(config)

    assert config.Config.GUILD_ID is None
    messages = [r.message for r in caplog.records]
    assert any("Invalid GUILD_ID" in m for m in messages)


def test_invalid_f1_channel_id_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("F1_CHANNEL_ID", "nan")

    import elbot.config as config

    with caplog.at_level(logging.WARNING):
        importlib.reload(config)

    assert config.Config.F1_CHANNEL_ID == 0
    messages = [r.message for r in caplog.records]
    assert any("Invalid F1_CHANNEL_ID" in m for m in messages)
