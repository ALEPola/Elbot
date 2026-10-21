import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nextcord
import pytest
from nextcord.ext import application_checks

from elbot.cogs.admin import ModerationCog, moderation_target_error


def interaction(*, allowed=False, guild=True, bot_allowed=True):
    user = SimpleNamespace(id=1, top_role=5)
    me = SimpleNamespace(id=2, top_role=10)
    server = SimpleNamespace(id=3, owner_id=4, me=me) if guild else None
    def permissions_for(member):
        if not guild:
            raise AttributeError("DM")
        return nextcord.Permissions.all() if (bot_allowed if member is me else allowed) else nextcord.Permissions.none()
    return SimpleNamespace(user=user, guild=server, client=SimpleNamespace(user=me),
                           channel=SimpleNamespace(permissions_for=permissions_for))


async def run_checks(command, context):
    for check in command.checks:
        result = check(context)
        if inspect.isawaitable(result):
            result = await result
        assert result


@pytest.mark.parametrize("name", ["kick", "ban", "clear_messages", "clear_bot_messages"])
async def test_moderation_rejects_unprivileged_users_and_dms(name):
    command = getattr(ModerationCog, name)
    command.parent_cog = ModerationCog(None)
    command.from_callback(command.callback)  # Match Nextcord's registration lifecycle.
    assert command.checks
    assert command.default_member_permissions is not None
    assert command.dm_permission is False
    with pytest.raises(nextcord.ApplicationCheckFailure):
        await run_checks(command, interaction())
    with pytest.raises(nextcord.ApplicationCheckFailure):
        await run_checks(command, interaction(allowed=True, guild=False))
    with pytest.raises(application_checks.ApplicationBotMissingPermissions):
        await run_checks(command, interaction(allowed=True, bot_allowed=False))
    await run_checks(command, interaction(allowed=True))


def test_moderation_enforces_requester_and_bot_hierarchy():
    ctx = interaction(allowed=True)
    target = SimpleNamespace(id=8, guild=ctx.guild, top_role=4)
    assert moderation_target_error(ctx, target) is None
    for uid, rank in [(ctx.user.id, 1), (ctx.guild.owner_id, 1), (2, 1), (8, 5), (8, 10)]:
        target.id, target.top_role = uid, rank
        assert moderation_target_error(ctx, target)
    ctx.user.id = ctx.guild.owner_id
    target.id, target.top_role = 8, 9
    assert moderation_target_error(ctx, target) is None
    target.top_role = 10
    assert moderation_target_error(ctx, target)


@pytest.mark.parametrize("name", ["clear_messages", "clear_bot_messages"])
@pytest.mark.parametrize("count", [-1, 0, 101, True])
async def test_message_deletion_rejects_unbounded_requests(monkeypatch, name, count):
    ctx = interaction(allowed=True)
    ctx.response = SimpleNamespace(defer=AsyncMock())
    ctx.channel.purge = AsyncMock()
    monkeypatch.setattr("elbot.cogs.admin.safe_reply", AsyncMock())
    await getattr(ModerationCog, name).callback(ModerationCog(None), ctx, count)
    ctx.channel.purge.assert_not_awaited()
