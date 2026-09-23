"""DJ and memory tools with authenticated request context and shared execution policy."""

from __future__ import annotations

import time

from elbot.music.actions import current_actor, ActionDenied
from typing import Awaitable, Callable


def _state(music, guild_id: int):
    return music._states.get(guild_id)


def build_tools(
    music, guild, controller=None, memory=None
) -> dict[str, Callable[[dict], Awaitable[dict]]]:
    async def get_current_track(_args: dict) -> dict:
        state = _state(music, guild.id)
        track = state.now_playing if state else None
        if track is None:
            return {"playing": False}
        elapsed_ms = 0
        if state.playback_started_at:
            elapsed_ms = int((time.monotonic() - state.playback_started_at) * 1000)
        duration_ms = track.handle.duration or 0
        return {
            "playing": True,
            "title": track.handle.title,
            "author": track.handle.author,
            "duration_ms": duration_ms,
            "position_ms": max(0, min(elapsed_ms, duration_ms)) if duration_ms else max(0, elapsed_ms),
            "requested_by": track.requester_display,
            "source": track.handle.source,
        }

    async def get_queue(args: dict) -> dict:
        state = _state(music, guild.id)
        if state is None:
            return {"count": 0, "queue": []}
        try:
            limit = max(1, min(int(args.get("limit") or 10), 25))
        except (TypeError, ValueError):
            limit = 10
        items = state.queue.snapshot()
        return {
            "count": len(items),
            "queue": [
                {"title": t.handle.title, "author": t.handle.author, "requested_by": t.requester_display}
                for t in items[:limit]
            ],
        }

    async def search_track(args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"results": []}
        try:
            tracks = await music.backend.resolve_tracks(query, prefer_search=True)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}
        return {
            "results": [
                {"title": t.title, "author": t.author, "duration_ms": t.duration}
                for t in tracks[:5]
            ],
        }

    async def get_requester(_args: dict) -> dict:
        state = _state(music, guild.id)
        track = state.now_playing if state else None
        return {"requested_by": track.requester_display if track else None}

    async def recommend_similar(_args: dict) -> dict:
        state = _state(music, guild.id)
        track = state.now_playing if state else None
        if track is None:
            return {"results": []}
        # Mirrors the existing autoplay continuation query in music.py.
        query = f"{track.handle.author} mix"
        try:
            tracks = await music.backend.resolve_tracks(query, prefer_search=True)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}
        current_title = track.handle.title
        results = [
            {"title": t.title, "author": t.author, "duration_ms": t.duration}
            for t in tracks if t.title != current_title
        ]
        return {"results": results[:3]}

    async def action(name, args):
        actor = current_actor.get()
        try:
            return await music.actions.execute(guild, actor, name, args)
        except ActionDenied as exc:
            return {"error": str(exc)}

    async def play_track(args):
        return await action("play", args)

    async def skip_track(args):
        return await action("skip", args)

    async def vote_to_skip(args):
        return await action("vote", args)

    async def pause_playback(args):
        return await action("pause", args)

    async def resume_playback(args):
        return await action("resume", args)

    async def set_volume(args):
        return await action("volume", args)

    async def remove_from_queue(args):
        return await action("remove", {"start": args["position"], "end": args["position"]})

    async def confirmation(name):
        actor = current_actor.get()
        if actor is None:
            return {"error": "No authenticated requester."}
        state = music._states.get(guild.id)
        channel = guild.get_channel(state.last_channel_id) if state and state.last_channel_id else None
        if channel is None:
            return {"error": "Use the slash command in a text channel to request confirmation."}
        try:
            return await music.actions.confirm(guild, actor, name, channel.send)
        except ActionDenied as exc:
            return {"error": str(exc)}

    async def clear_queue(args):
        return await confirmation("clear")

    async def stop_playback(args):
        return await confirmation("stop")

    async def memory_actor():
        actor = current_actor.get()
        if actor is None or not actor.valid():
            raise ActionDenied("No authenticated requester is attached to this action.")
        state = music.actions.state(actor)
        await music.actions.member(guild, actor, state, "memory")
        return actor

    async def remember_about_user(args):
        if memory is None:
            return {"error": "Memory is unavailable."}
        try:
            actor = await memory_actor()
        except ActionDenied as exc:
            return {"error": str(exc)}
        note = args.get("note", "").strip()
        if not note:
            return {"error": "no note given"}
        memory.remember(actor.user_id, note)
        return {"remembered": True}

    async def recall_about_user(args):
        if memory is None:
            return {"notes": []}
        try:
            actor = await memory_actor()
        except ActionDenied as exc:
            return {"error": str(exc)}
        return {"notes": memory.recall(actor.user_id)}

    return {
        "get_current_track": get_current_track,
        "get_queue": get_queue,
        "search_track": search_track,
        "get_requester": get_requester,
        "recommend_similar": recommend_similar,
        "play_track": play_track,
        "skip_track": skip_track,
        "vote_to_skip": vote_to_skip,
        "remove_from_queue": remove_from_queue,
        "clear_queue": clear_queue,
        "stop_playback": stop_playback,
        "pause_playback": pause_playback,
        "resume_playback": resume_playback,
        "set_volume": set_volume,
        "remember_about_user": remember_about_user,
        "recall_about_user": recall_about_user,
    }
