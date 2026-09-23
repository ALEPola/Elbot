"""Execution-time DJ policy shared by voice, slash commands and buttons."""

import asyncio
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
import math
import logging
import os
import time
from typing import Callable

import nextcord


class ActionDenied(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: int
    guild_id: int
    channel_id: int
    valid: Callable[[], bool] = lambda: True


current_actor: ContextVar[Actor | None] = ContextVar("music_actor", default=None)
logger = logging.getLogger("elbot.music.actions")
DESTRUCTIVE = {"clear", "stop", "disconnect"}
PRIVILEGED = {"skip", "clear", "stop", "disconnect", "move", "shuffle", "loop", "autoplay", "seek", "fair", "replay"}


def setting(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def privileged(member, state):
    permissions = member.guild_permissions
    role_ids = {int(value) for value in os.getenv("ELBOT_DJ_ROLE_IDS", "").split(",") if value.strip().isdigit()}
    return bool(
        permissions.administrator or permissions.manage_guild
        or member.id == state.owner_id
        or any(role.id in role_ids for role in member.roles)
    )


def fingerprint(state):
    return (getattr(state.now_playing, "id", None), tuple(track.id for track in state.queue.snapshot()))


class MusicActions:
    def __init__(self, music):
        self.music = music
        self.pending = {}  # one confirmation per guild/requester

    def state(self, actor):
        state = self.music._states.get(actor.guild_id)
        if state is None or state.player is None or state.player.channel.id != actor.channel_id:
            raise ActionDenied("The music session changed. Please request the action again.")
        return state

    async def member(self, guild, actor, state, action):
        if actor is None or actor.guild_id != guild.id or not actor.valid():
            raise ActionDenied("This voice request has expired. Please ask again.")
        try:
            member = await asyncio.wait_for(guild.fetch_member(actor.user_id), 5)
        except (nextcord.HTTPException, asyncio.TimeoutError):
            raise ActionDenied("Could not verify your current Discord permissions. Try again.") from None
        self.check(guild, actor, state, member, action)
        return member

    def check(self, guild, actor, state, member, action):
        if not actor.valid() or self.state(actor) is not state:
            raise ActionDenied("The request or music session expired. Please ask again.")
        voice_state = state.player.channel.voice_states.get(actor.user_id)
        if member.bot or voice_state is None:
            raise ActionDenied("Join ELBOT's voice channel to control playback.")
        if action in PRIVILEGED and not privileged(member, state):
            raise ActionDenied("Only the session owner, DJ, or server administrator can do that.")

    def rate_limit(self, state, actor):
        now = time.monotonic()
        for uid, times in list(state.request_times.items()):
            while times and now - times[0] >= 60:
                times.popleft()
            if not times:
                del state.request_times[uid]
        times = state.request_times.setdefault(actor.user_id, deque())
        if len(times) >= setting("ELBOT_REQUESTS_PER_MINUTE", 3):
            raise ActionDenied("Song request limit reached. Wait a minute before requesting again.")
        self.queue_limit(state, actor)
        times.append(now)  # failed resolution still counts, limiting expensive searches

    @staticmethod
    def queue_limit(state, actor):
        count = sum(t.requested_by == actor.user_id for t in state.queue.snapshot())
        if count >= setting("ELBOT_MAX_QUEUED_PER_USER", 3):
            raise ActionDenied("You already have the maximum number of queued requests.")

    async def execute(self, guild, actor, action, args=None, *, confirmed=None):
        try:
            result = await self._execute(guild, actor, action, args, confirmed=confirmed)
        except Exception:
            logger.info("DJ action denied or failed", extra={
                "guild_id": guild.id, "user_id": getattr(actor, "user_id", None), "action": action,
            })
            raise
        logger.info("DJ action completed", extra={
            "guild_id": guild.id, "user_id": actor.user_id, "action": action,
        })
        return result

    async def _execute(self, guild, actor, action, args=None, *, confirmed=None):
        if actor is None:
            raise ActionDenied("No authenticated requester is attached to this action.")
        args = args or {}
        state = self.state(actor)
        if action in DESTRUCTIVE and confirmed is None:
            raise ActionDenied("This action requires a Discord confirmation button.")
        async with state.action_lock:
            member = await self.member(guild, actor, state, action)
            if confirmed is not None and fingerprint(state) != confirmed:
                raise ActionDenied("Playback or the queue changed. Request a new confirmation.")
            if action in ("play", "replay"):
                if args.get("play_next") and not privileged(member, state):
                    raise ActionDenied("Only the session owner or a DJ can bypass fair queue order.")
                self.rate_limit(state, actor)
                if action == "replay":
                    if not privileged(member, state):
                        raise ActionDenied("Only the session owner or a DJ can replay a track.")
                    previous = state.queue.last_played
                    if previous is None:
                        raise ActionDenied("No previous track is available.")
                    entry = previous.clone()
                else:
                    query = args.get("query")
                    if not isinstance(query, str) or not query.strip() or len(query) > 500:
                        raise ActionDenied("Provide a song query of 1–500 characters.")
                    self.music.backend  # initialize fallback
                    try:
                        entry = await self.music.fallback.build_queue_entry(
                            query.strip(), requested_by=actor.user_id, requester_display=member.display_name,
                            channel_id=state.last_channel_id or 0,
                        )
                    except Exception:
                        raise ActionDenied("Could not resolve that song. Try a more specific title or link.") from None
                # Resolution can take seconds: re-fetch roles and recheck membership.
                member = await self.member(guild, actor, state, "skip" if args.get("play_next") else action)
                self.queue_limit(state, actor)
                entry.requested_by = actor.user_id
                entry.requester_display = member.display_name
                if args.get("play_next"):
                    state.queue.add_next(entry)
                else:
                    state.queue.add(entry)
                position = next(i for i, t in enumerate(state.queue.snapshot(), 1) if t.id == entry.id)
                await self.music._ensure_playing(guild.id)
                await self.music._refresh_now_playing(guild.id)
                return {"queued": True, "title": entry.handle.title, "author": entry.handle.author,
                        "queue_position": position}
            if action == "vote":
                current = state.now_playing
                if current is None:
                    return {"skipped": False, "message": "Nothing is playing right now."}
                if state.vote_track != current.id:
                    state.vote_track, state.skip_votes = current.id, set()
                eligible = set()
                for uid in list(state.player.channel.voice_states):
                    try:
                        participant = await asyncio.wait_for(guild.fetch_member(uid), 5)
                    except (nextcord.HTTPException, asyncio.TimeoutError):
                        raise ActionDenied("Could not verify the current listeners. Retry your vote.") from None
                    if not participant.bot:
                        eligible.add(uid)
                self.check(guild, actor, state, member, action)
                if state.now_playing is not current:
                    raise ActionDenied("The track changed; vote again for the new track.")
                eligible.intersection_update(state.player.channel.voice_states)
                state.skip_votes.intersection_update(eligible)
                state.skip_votes.add(actor.user_id)
                needed = max(1, math.floor(len(eligible) / 2) + 1)
                if len(state.skip_votes) < needed:
                    return {"skipped": False, "message": f"Skip votes: {len(state.skip_votes)}/{needed}."}
                action = "skip"
            if action == "skip":
                success, message = await self.music._skip_current(guild.id, state)
                state.skip_votes.clear()
                return {"skipped": success, "message": message}
            if action in ("pause", "resume"):
                if action == "pause":
                    await state.player.pause()
                else:
                    await state.player.resume()
                result = {"paused": action == "pause", "message": f"Playback {'paused' if action == 'pause' else 'resumed'}."}
            elif action == "volume":
                level = args.get("level")
                if type(level) is not int or not 0 <= level <= 200:
                    raise ActionDenied("Volume must be an integer from 0 to 200.")
                if not privileged(member, state) and (level > 100 or abs(level - state.volume) > 20):
                    raise ActionDenied("Listeners may adjust volume by up to 20 points, within 0–100.")
                await state.player.set_volume(level)
                state.volume = level
                result = {"volume": level, "message": f"Volume set to {level}%."}
            elif action == "remove":
                start, end = args["start"], args["end"]
                tracks = state.queue.snapshot()
                if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(tracks):
                    raise ActionDenied("Choose a valid queue position or range.")
                if not privileged(member, state) and any(t.requested_by != actor.user_id for t in tracks[start-1:end]):
                    raise ActionDenied("You may remove only your own requests.")
                removed = state.queue.remove_range(start-1, end-1)
                result = {"removed": len(removed), "message": f"Removed {len(removed)} request(s)."}
            elif action == "clear":
                count = len(state.queue)
                state.queue.clear()
                result = {"cleared": count, "message": f"Cleared {count} queued request(s)."}
            elif action == "stop":
                await self.music._stop(guild.id)
                return {"message": "Playback stopped and queue cleared."}
            elif action == "disconnect":
                await self.music._disconnect(guild.id, state)
                return {"message": "Disconnected and cleared the queue."}
            elif action == "seek":
                duration = state.now_playing.handle.duration if state.now_playing else 0
                if not state.now_playing or args["position"] < 0 or (duration and args["position"] >= duration):
                    raise ActionDenied("Choose a position within the current track.")
                await state.player.seek(args["position"])
                result = {"message": "Playback position updated."}
            elif action == "loop":
                if args["mode"] not in ("off", "track", "queue"):
                    raise ActionDenied("Invalid loop mode.")
                state.loop_mode = args["mode"]
                result = {"message": f"Loop mode: {state.loop_mode}."}
            elif action == "autoplay":
                state.autoplay = args["enabled"]
                result = {"message": f"AutoPlay {'on' if state.autoplay else 'off'}."}
            elif action == "fair":
                state.queue.set_fair(args["enabled"])
                result = {"message": f"Fair queuing {'on' if args['enabled'] else 'off'}."}
            elif action == "shuffle":
                if state.queue.fair:
                    raise ActionDenied("Turn fair queuing off before shuffling or moving requests.")
                state.queue.shuffle()
                result = {"message": "Queue shuffled."}
            elif action == "move":
                if state.queue.fair:
                    raise ActionDenied("Turn fair queuing off before shuffling or moving requests.")
                if not 1 <= args["destination"] <= len(state.queue) or not state.queue.move(
                    args["source"] - 1, args["destination"] - 1,
                ):
                    raise ActionDenied("Choose valid queue positions.")
                result = {"message": "Queue order updated."}
            else:
                raise ActionDenied("Unknown music action.")
            await self.music._refresh_now_playing(guild.id)
            return result

    async def confirm(self, guild, actor, action, send):
        state = self.state(actor)
        await self.member(guild, actor, state, action)
        key = (guild.id, actor.user_id)
        previous = self.pending.pop(key, None)
        if previous:
            previous.stop()
        view = ActionConfirmation(self, guild, actor, action, state)
        self.pending[key] = view
        await send(
            f"<@{actor.user_id}>: confirm **{action}** for {len(state.queue)} queued request(s)"
            + (" and current playback" if action != "clear" else "") + ". Expires in 30 seconds.",
            view=view, allowed_mentions=nextcord.AllowedMentions.none(),
        )
        return {"confirmation_required": True, "message": "Use your Confirm button in Discord within 30 seconds."}


class ActionConfirmation(nextcord.ui.View):
    def __init__(self, actions, guild, actor, action, state):
        super().__init__(timeout=30)
        self.actions, self.guild, self.actor, self.action = actions, guild, actor, action
        self.state = state
        self.expected = fingerprint(state)
        self.expires = time.monotonic() + 30
        self.used = False

    async def interaction_check(self, interaction):
        if (interaction.guild is None or interaction.user.id != self.actor.user_id
                or interaction.guild.id != self.guild.id):
            await interaction.response.send_message("Only the requester can confirm this action.", ephemeral=True)
            return False
        return True

    @nextcord.ui.button(label="Confirm", style=nextcord.ButtonStyle.danger)
    async def approve(self, button, interaction):
        if not await self.interaction_check(interaction):
            return
        key = (self.guild.id, self.actor.user_id)
        if self.used or time.monotonic() >= self.expires or self.actions.pending.get(key) is not self:
            await interaction.response.send_message("This confirmation has expired.", ephemeral=True)
            return
        self.used = True  # consume before any await: double clicks cannot execute twice
        self.actions.pending.pop(key, None)
        await interaction.response.defer(ephemeral=True)
        try:
            if self.actions.state(self.actor) is not self.state:
                raise ActionDenied("The music session changed. Request a new confirmation.")
            result = await self.actions.execute(self.guild, self.actor, self.action, confirmed=self.expected)
            message = result["message"]
        except ActionDenied as exc:
            message = str(exc)
        self.stop()
        await interaction.edit_original_message(content=message, view=None)

    @nextcord.ui.button(label="Cancel", style=nextcord.ButtonStyle.secondary)
    async def cancel(self, button, interaction):
        if not await self.interaction_check(interaction):
            return
        self.used = True
        self.stop()
        key = (self.guild.id, self.actor.user_id)
        if self.actions.pending.get(key) is self:
            self.actions.pending.pop(key, None)
        await interaction.response.edit_message(content="Cancelled.", view=None)

    async def on_timeout(self):
        key = (self.guild.id, self.actor.user_id)
        if self.actions.pending.get(key) is self:
            self.actions.pending.pop(key, None)
