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


def test_port_range_env_overrides(monkeypatch):
    monkeypatch.setenv("AUTO_LAVALINK_PORT_START", "45000")
    monkeypatch.setenv("AUTO_LAVALINK_PORT_TRIES", "3")

    module = _reload_auto_lavalink()

    attempts: list[int] = []

    class DummySocket:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
            return False

        def setsockopt(self, *_args, **_kwargs):
            return None

        def bind(self, addr):
            attempts.append(addr[1])
            if addr[1] != module.AUTO_LAVALINK_PORT_START:
                raise OSError("port in use")

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.socket, "socket", DummySocket)

    port = module._find_free_port()

    assert port == module.AUTO_LAVALINK_PORT_START
    assert attempts == [module.AUTO_LAVALINK_PORT_START]

    monkeypatch.delenv("AUTO_LAVALINK_PORT_START", raising=False)
    monkeypatch.delenv("AUTO_LAVALINK_PORT_TRIES", raising=False)
    _reload_auto_lavalink()
