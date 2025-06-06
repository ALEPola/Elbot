import importlib
import os

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
