from pathlib import Path

import pytest

from elbot.core import deploy, docker_tasks


class _DummyError(RuntimeError):
    pass


def test_install_service_systemd(monkeypatch, tmp_path: Path) -> None:
    recorded: dict[str, tuple[Path, bool, str]] = {}

    def fake_install(root: Path, *, require_lavalink: bool, python_executable: str) -> None:
        recorded["systemd"] = (root, require_lavalink, python_executable)

    monkeypatch.setattr(deploy.service_install, "install_systemd_service", fake_install)

    deploy.install_service(
        tmp_path,
        python_executable="/venv/bin/python",
        require_lavalink=True,
        platform_override="systemd",
    )

    assert recorded["systemd"] == (tmp_path, True, "/venv/bin/python")


def test_install_service_windows(monkeypatch, tmp_path: Path) -> None:
    recorded: dict[str, tuple[Path, str]] = {}

    def fake_install(root: Path, *, python_executable: str) -> None:
        recorded["windows"] = (root, python_executable)

    monkeypatch.setattr(deploy.service_install, "install_windows_service", fake_install)

    deploy.install_service(
        tmp_path,
        python_executable="C:/venv/Scripts/python.exe",
        platform_override="windows",
    )

    assert recorded["windows"] == (tmp_path, "C:/venv/Scripts/python.exe")


def test_remove_service_launchd(monkeypatch) -> None:
    called = {}

    def fake_remove() -> None:
        called["launchd"] = True

    monkeypatch.setattr(deploy.service_install, "uninstall_launchd_service", fake_remove)

    deploy.remove_service(platform_override="darwin")

    assert called["launchd"] is True


def test_control_service_windows_maps_actions():
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    deploy.control_service(
        "start",
        is_windows=True,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
        windows_service_name="CustomElbot",
    )

    assert captured == [["sc", "start", "CustomElbot"]]


def test_control_service_windows_invalid_action():
    with pytest.raises(_DummyError):
        deploy.control_service(
            "enable",
            is_windows=True,
            run=lambda _: None,
            ensure_command=lambda _: True,
            error_cls=_DummyError,
        )


def test_control_service_systemd_requires_systemctl():
    with pytest.raises(_DummyError):
        deploy.control_service(
            "start",
            is_windows=False,
            run=lambda _: None,
            ensure_command=lambda _: False,
            error_cls=_DummyError,
        )


def test_control_service_systemd_status_runs_command():
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    deploy.control_service(
        "status",
        is_windows=False,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
    )

    assert captured == [["systemctl", "status", deploy.SERVICE_NAME_DEFAULT]]


def test_control_service_systemd_invalid_action():
    with pytest.raises(_DummyError):
        deploy.control_service(
            "reload",
            is_windows=False,
            run=lambda _: None,
            ensure_command=lambda _: True,
            error_cls=_DummyError,
        )


def test_control_service_systemd_start():
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    deploy.control_service(
        "start",
        is_windows=False,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
    )

    assert captured == [["systemctl", "start", deploy.SERVICE_NAME_DEFAULT]]


def test_docker_run_compose_actions(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("version: '3'\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        calls.append(args)

    docker_tasks.run_compose_action(
        "up",
        docker_dir=tmp_path,
        run=fake_run,
        remove_orphans=True,
        error_cls=_DummyError,
    )
    docker_tasks.run_compose_action(
        "down",
        docker_dir=tmp_path,
        run=fake_run,
        error_cls=_DummyError,
    )
    docker_tasks.run_compose_action(
        "pull",
        docker_dir=tmp_path,
        run=fake_run,
        error_cls=_DummyError,
    )
    docker_tasks.run_compose_action(
        "logs",
        docker_dir=tmp_path,
        run=fake_run,
        follow=True,
        error_cls=_DummyError,
    )

    base = ["docker", "compose", "-f", str(compose)]
    assert calls == [
        base + ["up", "-d", "--build", "--remove-orphans"],
        base + ["down"],
        base + ["pull"],
        base + ["logs", "-f"],
    ]


def test_docker_unknown_action_raises(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("", encoding="utf-8")

    with pytest.raises(_DummyError):
        docker_tasks.run_compose_action(
            "restart",
            docker_dir=tmp_path,
            run=lambda _: None,
            error_cls=_DummyError,
        )


def test_docker_missing_compose(tmp_path: Path) -> None:
    with pytest.raises(_DummyError):
        docker_tasks.ensure_compose_file(tmp_path, error_cls=_DummyError)
