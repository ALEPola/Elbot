"""Phase 5/6: DJ tools exposed to GPT-Live's backend model.

Phase 5 tools (get_current_track, get_queue, search_track, get_requester,
recommend_similar) only read Music cog state. Phase 6 adds tools that
mutate playback (play_track, skip_track, pause_playback, resume_playback,
set_volume) on behalf of whoever is currently addressing ELBOT in voice -
GPT-Live only forwards audio from someone speaking in the bot's own voice
channel, so no separate membership check is needed here (mirrors the one
`Music._control_error` does for slash commands). Destructive actions
(stop/clear queue) are deliberately not exposed yet; see Phase 7 in
ELBOT_GPT_LIVE_BATTLE_PLAN.md for the planned confirmation/permission
layer before those are added. Each tool takes a JSON-schema-shaped
`arguments` dict (already parsed) and returns a JSON-serializable dict.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable


def _state(music, guild_id: int):
    return music._states.get(guild_id)


def build_tools(
    music, guild, controller=None
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

    def _speaker_identity() -> tuple[int, str]:
        if controller is not None and controller.active_speaker:
            return controller.active_speaker, controller.active_name or "voice request"
        bot_user = getattr(music.bot, "user", None)
        return getattr(bot_user, "id", 0), "voice request"

    async def play_track(args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "no query given"}
        state = music._get_state(guild.id)
        if state.player is None:
            return {"error": "not connected to a voice channel"}
        play_next = bool(args.get("play_next"))
        requested_by, requester_display = _speaker_identity()
        music.backend  # ensure the lazily-built fallback pipeline exists
        async with state.lock:
            try:
                entry = await music.fallback.build_queue_entry(
                    query,
                    requested_by=requested_by,
                    requester_display=requester_display,
                    channel_id=state.last_channel_id or 0,
                )
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"[:200]}
            if play_next:
                state.queue.add_next(entry)
                position = 1
            else:
                state.queue.add(entry)
                position = len(state.queue)
        await music._ensure_playing(guild.id)
        await music._refresh_now_playing(guild.id)
        return {
            "queued": True,
            "title": entry.handle.title,
            "author": entry.handle.author,
            "queue_position": position,
        }

    async def skip_track(_args: dict) -> dict:
        state = _state(music, guild.id)
        if state is None:
            return {"skipped": False, "message": "Nothing is playing right now."}
        skipped, message = await music._skip_current(guild.id, state)
        return {"skipped": skipped, "message": message}

    async def pause_playback(_args: dict) -> dict:
        state = _state(music, guild.id)
        if state is None or state.player is None:
            return {"paused": False, "message": "Nothing is playing right now."}
        if getattr(state.player, "paused", False):
            return {"paused": True, "message": "Already paused."}
        await state.player.pause()
        await music._refresh_now_playing(guild.id)
        return {"paused": True, "message": "Playback paused."}

    async def resume_playback(_args: dict) -> dict:
        state = _state(music, guild.id)
        if state is None or state.player is None:
            return {"paused": False, "message": "Nothing is playing right now."}
        if not getattr(state.player, "paused", False):
            return {"paused": False, "message": "Already playing."}
        await state.player.resume()
        await music._refresh_now_playing(guild.id)
        return {"paused": False, "message": "Playback resumed."}

    async def set_volume(args: dict) -> dict:
        state = _state(music, guild.id)
        if state is None or state.player is None:
            return {"error": "not connected to a voice channel"}
        try:
            level = int(args.get("level"))
        except (TypeError, ValueError):
            return {"error": "level must be an integer"}
        level = max(0, min(200, level))
        state.volume = level
        await state.player.set_volume(level)
        await music._refresh_now_playing(guild.id)
        return {"volume": level}

    return {
        "get_current_track": get_current_track,
        "get_queue": get_queue,
        "search_track": search_track,
        "get_requester": get_requester,
        "recommend_similar": recommend_similar,
        "play_track": play_track,
        "skip_track": skip_track,
        "pause_playback": pause_playback,
        "resume_playback": resume_playback,
        "set_volume": set_volume,
    }
