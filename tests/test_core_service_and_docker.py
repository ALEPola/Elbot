import pytest

from pathlib import Path

from elbot.core import docker_tasks, service_manager


class _DummyError(RuntimeError):
    pass


def test_install_service_passes_flags(monkeypatch):
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    service_manager.install_service(fake_run, require_lavalink=True, force=True)

    assert captured == [["-m", "elbot.service_install", "--require-lavalink", "--force"]]


def test_remove_service_invokes_runner():
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    service_manager.remove_service(fake_run)
    assert captured == [["-m", "elbot.service_install", "--remove"]]


def test_control_service_windows_maps_actions():
    captured: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        captured.append(args)

    service_manager.control_service(
        "start",
        is_windows=True,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
    )

    assert captured == [["sc", "start", "Elbot"]]


def test_control_service_windows_invalid_action():
    with pytest.raises(_DummyError):
        service_manager.control_service(
            "enable",
            is_windows=True,
            run=lambda _: None,
            ensure_command=lambda _: True,
            error_cls=_DummyError,
        )


def test_control_service_systemd_requires_systemctl():
    with pytest.raises(_DummyError):
        service_manager.control_service(
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

    service_manager.control_service(
        "status",
        is_windows=False,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
    )

    assert captured == [["systemctl", "status", "elbot.service"]]


def test_control_service_systemd_invalid_action():
    with pytest.raises(_DummyError):
        service_manager.control_service(
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

    service_manager.control_service(
        "start",
        is_windows=False,
        run=fake_run,
        ensure_command=lambda _: True,
        error_cls=_DummyError,
    )

    assert captured == [["systemctl", "start", "elbot.service"]]


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
