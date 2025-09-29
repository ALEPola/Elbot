import importlib
import logging
import pytest


def test_config_validate_missing_env(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    import elbot.config as config

    importlib.reload(config)
    with pytest.raises(SystemExit):
        config.Config.validate()


def test_config_validate_pass(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("AUTO_LAVALINK", "0")
    monkeypatch.setenv("LAVALINK_HOST", "ll")
    monkeypatch.setenv("LAVALINK_PASSWORD", "pw")
    import elbot.config as config

    importlib.reload(config)
    config.Config.validate()


def test_invalid_guild_id_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("GUILD_ID", "abc")

    import elbot.config as config

    with caplog.at_level(logging.WARNING):
        importlib.reload(config)

    assert config.Config.GUILD_ID is None
    messages = [r.message for r in caplog.records]
    assert any("Invalid GUILD_ID" in m for m in messages)


def test_invalid_f1_channel_id_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("F1_CHANNEL_ID", "nan")

    import elbot.config as config

    with caplog.at_level(logging.WARNING):
        importlib.reload(config)

    assert config.Config.F1_CHANNEL_ID == 0
    messages = [r.message for r in caplog.records]
    assert any("Invalid F1_CHANNEL_ID" in m for m in messages)


def test_default_lavalink_port_when_unset(monkeypatch):
    monkeypatch.delenv("LAVALINK_PORT", raising=False)

    import elbot.config as config

    importlib.reload(config)

    assert config.Config.LAVALINK_PORT == config.DEFAULT_LAVALINK_PORT
