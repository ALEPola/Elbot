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


def test_write_conf_writes_port_and_optional_address(monkeypatch, tmp_path):
    monkeypatch.setenv("ELBOT_DATA_DIR", str(tmp_path))

    module = _reload_auto_lavalink()

    module._write_conf(2444, "pw", "0.0.0.0")
    lines = module.CONF.read_text().splitlines()

    server_idx = lines.index("  server:")
    assert lines[server_idx + 1] == "    port: 2444"
    assert lines[server_idx + 2] == '    address: "0.0.0.0"'
    assert lines[server_idx + 3] == '    password: "pw"'

    module._write_conf(2666, "pw2", None)
    lines = module.CONF.read_text().splitlines()
    server_idx = lines.index("  server:")
    assert lines[server_idx + 1] == "    port: 2666"
    assert lines[server_idx + 2] == '    password: "pw2"'
