"""Runtime helpers for Elbot CLI refactor."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable, Iterable

RunFunc = Callable[[list[str]], object]
EchoFunc = Callable[[str], None]


def venv_python(venv_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_pip(venv_dir: Path, *, is_windows: bool) -> Path:
    if is_windows:
        return venv_dir / "Scripts" / "pip.exe"
    return venv_dir / "bin" / "pip"


def create_venv(
    venv_dir: Path,
    *,
    force: bool = False,
    run: RunFunc,
    echo: EchoFunc | None = None,
    python_executable: str | None = None,
) -> None:
    python_executable = python_executable or sys.executable
    if venv_dir.exists():
        if not force:
            return
        shutil.rmtree(venv_dir)
    if echo:
        echo("Creating virtual environment (.venv)...")
    run([python_executable, "-m", "venv", str(venv_dir)])


def run_in_venv(
    args: Iterable[str],
    *,
    venv_dir: Path,
    is_windows: bool,
    run: RunFunc,
    error_cls: type[Exception] = RuntimeError,
):
    python = venv_python(venv_dir, is_windows=is_windows)
    if not python.exists():
        raise error_cls("virtual environment not found; run 'elbotctl install' first")
    return run([str(python), *list(args)])


def pip_install(
    args: Iterable[str],
    *,
    venv_dir: Path,
    is_windows: bool,
    run: RunFunc,
    error_cls: type[Exception] = RuntimeError,
) -> None:
    pip = venv_pip(venv_dir, is_windows=is_windows)
    if not pip.exists():
        raise error_cls("pip not available in the virtual environment")
    run([str(pip), *list(args)])
