import pytest

from elbot.core import prerequisites


def test_find_missing_detects_commands():
    missing = prerequisites.find_missing(["ffmpeg", "java"], lambda name: name == "java")
    assert missing == ["ffmpeg"]


def test_ensure_prerequisites_no_missing():
    calls = []

    result = prerequisites.ensure_prerequisites(
        install_packages=True,
        non_interactive=False,
        platform_name="Linux",
        ensure_command=lambda _: True,
        which=lambda _: "bin",
        run=lambda args: calls.append(args),
        echo=lambda msg: calls.append([msg]),
        input_fn=lambda _: "y",
    )

    assert result == []
    assert calls == []


def test_ensure_prerequisites_auto_install(monkeypatch):
    commands_run = []
    responses = iter(["",])  # default to Enter meaning yes

    result = prerequisites.ensure_prerequisites(
        install_packages=True,
        non_interactive=False,
        platform_name="linux",
        ensure_command=lambda name: False if name == "ffmpeg" else True,
        which=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
        run=lambda args: commands_run.append(args),
        echo=lambda _: None,
        input_fn=lambda _: next(responses),
        commands=("ffmpeg",),
    )

    assert result == []
    assert commands_run == [["sudo", "apt-get", "install", "-y", "ffmpeg"]]


def test_ensure_prerequisites_decline_install():
    echoes: list[str] = []
    commands_run: list[list[str]] = []

    result = prerequisites.ensure_prerequisites(
        install_packages=True,
        non_interactive=False,
        platform_name="linux",
        ensure_command=lambda name: False if name == "ffmpeg" else True,
        which=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
        run=lambda args: commands_run.append(args),
        echo=lambda msg: echoes.append(msg),
        input_fn=lambda _: "n",
        commands=("ffmpeg",),
    )

    assert result == ["ffmpeg"]
    assert commands_run == []
    assert echoes[-1] == "Please install the missing tools manually and re-run the installer."


def test_ensure_prerequisites_no_auto_install():
    echoes: list[str] = []

    result = prerequisites.ensure_prerequisites(
        install_packages=False,
        non_interactive=True,
        platform_name="Windows",
        ensure_command=lambda _: False,
        which=lambda _: None,
        run=lambda _: None,
        echo=lambda msg: echoes.append(msg),
        commands=("git",),
    )

    assert result == ["git"]
    assert echoes[0].startswith("Missing prerequisites detected: ")
    assert echoes[-1] == "Please install the missing tools manually and re-run the installer."
