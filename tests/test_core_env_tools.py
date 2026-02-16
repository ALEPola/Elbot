import pytest

from pathlib import Path

from elbot.core import ops


class _DummyError(RuntimeError):
    pass


def test_read_write_update(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    ops.write_env(env_path, {"B": "2", "A": "1"})
    assert env_path.read_text(encoding="utf-8") == "A=1\nB=2\n"

    ops.update_env_var(env_path, "C", "3")
    env = ops.read_env(env_path)
    assert env == {"A": "1", "B": "2", "C": "3"}


def test_ensure_env_file_copies_example(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("TOKEN=abc\n", encoding="utf-8")

    ops.ensure_env_file(env_path, example)

    assert env_path.exists()
    assert env_path.read_text(encoding="utf-8") == "TOKEN=abc\n"


def test_prompt_env_handles_required_and_optional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("# sample\n", encoding="utf-8")

    prompts: list[str] = []
    secrets: list[str] = []

    def fake_input(message: str) -> str:
        prompts.append(message)
        return "nickname"

    def fake_getpass(message: str) -> str:
        secrets.append(message)
        return "super-secret"

    ops.prompt_env(
        env_path,
        example,
        non_interactive=False,
        overrides={"DISCORD_TOKEN": "abc"},
        required={"DISCORD_TOKEN": "Discord bot token"},
        optional={"OPENAI_API_KEY": "OpenAI API key", "ELBOT_USERNAME": "Bot username"},
        error_cls=_DummyError,
        input_fn=fake_input,
        secret_input_fn=fake_getpass,
    )

    env = ops.read_env(env_path)
    assert env["DISCORD_TOKEN"] == "abc"
    assert env["OPENAI_API_KEY"] == "super-secret"
    assert env["ELBOT_USERNAME"] == "nickname"
    assert prompts == ["Bot username [Elbot]: "]
    assert secrets == ["OpenAI API key [skip]: "]


def test_prompt_env_missing_required_non_interactive(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("", encoding="utf-8")

    with pytest.raises(_DummyError):
        ops.prompt_env(
            env_path,
            example,
            non_interactive=True,
            overrides={},
            required={"DISCORD_TOKEN": "Discord bot token"},
            optional={},
            error_cls=_DummyError,
        )


def test_prompt_env_non_interactive_uses_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_path = tmp_path / ".env"
    example = tmp_path / ".env.example"
    example.write_text("", encoding="utf-8")

    monkeypatch.setenv("DISCORD_TOKEN", "from-env")

    ops.prompt_env(
        env_path,
        example,
        non_interactive=True,
        overrides=None,
        required={"DISCORD_TOKEN": "Discord bot token"},
        optional={},
        error_cls=_DummyError,
    )

    env = ops.read_env(env_path)
    assert env["DISCORD_TOKEN"] == "from-env"
