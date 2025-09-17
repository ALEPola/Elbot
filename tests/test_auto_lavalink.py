import importlib
import sys


def _reload_auto_lavalink():
    sys.modules.pop("elbot.auto_lavalink", None)
    return importlib.import_module("elbot.auto_lavalink")


def test_env_override_skips_platformdirs(monkeypatch, tmp_path):
    monkeypatch.setenv("ELBOT_DATA_DIR", str(tmp_path))

    def _fail_user_data_dir(*_args, **_kwargs):
        raise AssertionError("user_data_dir should not be called when ELBOT_DATA_DIR is set")

    monkeypatch.setattr("platformdirs.user_data_dir", _fail_user_data_dir)

    module = _reload_auto_lavalink()
    assert module.APP_DIR == tmp_path
    assert module.BASE == tmp_path


def test_default_app_dir_uses_generic_author(monkeypatch, tmp_path):
    monkeypatch.delenv("ELBOT_DATA_DIR", raising=False)

    captured: dict[str, str] = {}

    def _fake_user_data_dir(appname, appauthor):
        captured["appname"] = appname
        captured["appauthor"] = appauthor
        return tmp_path

    monkeypatch.setattr("platformdirs.user_data_dir", _fake_user_data_dir)

    module = _reload_auto_lavalink()
    assert module.APP_DIR == tmp_path
    assert module.BASE == tmp_path
    assert captured == {"appname": "Elbot", "appauthor": "ElbotTeam"}
