import asyncio
import time
from unittest.mock import AsyncMock

import nextcord
from nextcord.ext import commands

from elbot.cogs import ai as ai_cog


class DummyInteraction:
    def __init__(self):
        self.user = type("User", (), {"id": 123})()
        self.response = type(
            "Resp",
            (),
            {
                "defer": AsyncMock(),
                "send_message": AsyncMock(),
            },
        )()
        self.followup = type("Follow", (), {"send": AsyncMock()})()


def test_chat_response_truncated(monkeypatch):
    long_content = "x" * 2100

    class DummyCompletion:
        def __init__(self, content):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {"message": type("Msg", (), {"content": content})()},
                )()
            ]

    class DummyOpenAI:
        def __init__(self, content):
            self.chat = type(
                "Chat",
                (),
                {
                    "completions": type(
                        "Completions",
                        (),
                        {"create": lambda self, **_: DummyCompletion(content)},
                    )(),
                },
            )()

    monkeypatch.setattr(ai_cog, "openai_client", DummyOpenAI(long_content))

    async def fake_to_thread(func, *a, **k):
        return func()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    intents = nextcord.Intents.none()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = ai_cog.AICog(bot)

    interaction = DummyInteraction()
    asyncio.run(cog._handle_chat(interaction, message="hello"))

    sent = interaction.followup.send.call_args.args[0]
    assert len(sent) <= ai_cog.MAX_RESPONSE_LENGTH
    assert sent.endswith("...")
    loop.close()


def test_chat_history(monkeypatch):
    recorded = []

    class DummyCompletion:
        def __init__(self, content):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {"message": type("Msg", (), {"content": content})()},
                )()
            ]

    class DummyOpenAI:
        def __init__(self):
            def create(self, model, messages):
                recorded.append(messages)
                return DummyCompletion("ok")

            self.chat = type(
                "Chat", (), {"completions": type("Completions", (), {"create": create})()}
            )()

    monkeypatch.setattr(ai_cog, "openai_client", DummyOpenAI())

    async def fake_to_thread(func, *a, **k):
        return func()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    def allow_requests(cache, user_id, *, rate_limit=ai_cog.RATE_LIMIT_SECONDS):
        now = time.monotonic()
        cache[user_id] = now
        return True, now

    monkeypatch.setattr(ai_cog, "_allow_request", allow_requests)

    intents = nextcord.Intents.none()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = ai_cog.AICog(bot)
    interaction = DummyInteraction()

    asyncio.run(cog._handle_chat(interaction, message="hi"))
    asyncio.run(cog._handle_chat(interaction, message="again"))

    assert len(recorded) == 2
    assert recorded[1][0]["content"] == "hi"
    assert recorded[1][-1]["content"] == "again"
    loop.close()


def test_chat_summary(monkeypatch, tmp_path):
    class DummyCompletion:
        def __init__(self, content):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {"message": type("Msg", (), {"content": content})()},
                )()
            ]

    class DummyOpenAI:
        def __init__(self):
            def create(self, model, messages):
                DummyOpenAI.last = messages
                return DummyCompletion("summary")

            self.chat = type(
                "Chat", (), {"completions": type("Completions", (), {"create": create})()}
            )()

    monkeypatch.setattr(ai_cog, "openai_client", DummyOpenAI())

    async def fake_to_thread(func, *a, **k):
        return func()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(ai_cog.Config, "BASE_DIR", tmp_path)

    intents = nextcord.Intents.none()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = commands.Bot(command_prefix="!", intents=intents, loop=loop)
    cog = ai_cog.AICog(bot)
    interaction = DummyInteraction()

    cog._persist_history(123, "user", "hi")
    cog._persist_history(123, "assistant", "there")

    asyncio.run(cog.ai_chat_summary(interaction))

    assert any("hi" in m["content"] for m in DummyOpenAI.last)
    loop.close()
