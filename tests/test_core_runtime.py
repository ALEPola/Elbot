import pytest

from pathlib import Path

from elbot.core import ops


class _DummyError(RuntimeError):
    pass


def test_venv_python_paths(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    assert ops.venv_python(venv_dir, is_windows=False) == venv_dir / "bin" / "python"
    assert ops.venv_python(venv_dir, is_windows=True) == venv_dir / "Scripts" / "python.exe"


def test_create_venv_invokes_runner(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        calls.append(args)
        (venv_dir / "bin").mkdir(parents=True, exist_ok=True)

    ops.create_venv(venv_dir, run=fake_run, echo=None, python_executable="python")

    assert calls == [["python", "-m", "venv", str(venv_dir)]]
    assert venv_dir.exists()


def test_create_venv_force_removes_existing(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    (venv_dir / "placeholder").parent.mkdir(parents=True, exist_ok=True)
    placeholder = venv_dir / "placeholder"
    placeholder.write_text("temp", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        calls.append(args)
        (venv_dir / "bin").mkdir(parents=True, exist_ok=True)

    ops.create_venv(venv_dir, force=True, run=fake_run, echo=None, python_executable="python")

    assert not placeholder.exists()
    assert venv_dir.exists()
    assert calls == [["python", "-m", "venv", str(venv_dir)]]


def _prepare_python(venv_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        target = venv_dir / "Scripts"
        target.mkdir(parents=True, exist_ok=True)
        python_path = target / "python.exe"
    else:
        target = venv_dir / "bin"
        target.mkdir(parents=True, exist_ok=True)
        python_path = target / "python"
    python_path.write_text("", encoding="utf-8")
    return python_path


def test_run_in_venv_executes_command(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    python_path = _prepare_python(venv_dir, is_windows=False)

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        calls.append(args)

    ops.run_in_venv(
        ["-m", "module"],
        venv_dir=venv_dir,
        is_windows=False,
        run=fake_run,
        error_cls=_DummyError,
    )

    assert calls == [[str(python_path), "-m", "module"]]


def test_run_in_venv_missing_python_raises(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"

    with pytest.raises(_DummyError):
        ops.run_in_venv(
            ["-m", "module"],
            venv_dir=venv_dir,
            is_windows=False,
            run=lambda _: None,
            error_cls=_DummyError,
        )


def test_pip_install_invokes_runner(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"
    pip_path = _prepare_python(venv_dir, is_windows=False).with_name("pip")
    pip_path.write_text("", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> None:
        calls.append(args)

    ops.pip_install(
        ["install", "pkg"],
        venv_dir=venv_dir,
        is_windows=False,
        run=fake_run,
        error_cls=_DummyError,
    )

    assert calls == [[str(pip_path), "install", "pkg"]]


def test_pip_install_missing_binary_raises(tmp_path: Path) -> None:
    venv_dir = tmp_path / ".venv"

    with pytest.raises(_DummyError):
        ops.pip_install(
            ["install", "pkg"],
            venv_dir=venv_dir,
            is_windows=False,
            run=lambda _: None,
            error_cls=_DummyError,
        )
