"""Phase 5: read-only DJ tools exposed to GPT-Live's backend model.

Every function here only reads Music cog state — none may queue, play,
skip, or otherwise mutate playback. Each takes a JSON-schema-shaped
`arguments` dict (already parsed) and returns a JSON-serializable dict.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable


def _state(music, guild_id: int):
    return music._states.get(guild_id)


def build_tools(music, guild) -> dict[str, Callable[[dict], Awaitable[dict]]]:
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

    return {
        "get_current_track": get_current_track,
        "get_queue": get_queue,
        "search_track": search_track,
        "get_requester": get_requester,
        "recommend_similar": recommend_similar,
    }
