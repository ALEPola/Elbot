import asyncio
from unittest.mock import AsyncMock

import nextcord
from nextcord.ext import commands

from cogs import chat as chat_cog


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
            self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})()})()]

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

    monkeypatch.setattr(chat_cog, "openai_client", DummyOpenAI(long_content))

    async def fake_to_thread(func, *a, **k):
        return func()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    intents = nextcord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    cog = chat_cog.ChatCog(bot)

    interaction = DummyInteraction()
    asyncio.run(cog.chat(interaction, message="hello"))

    sent = interaction.followup.send.call_args.args[0]
    assert len(sent) <= chat_cog.MAX_RESPONSE_LENGTH
    assert sent.endswith("...")
