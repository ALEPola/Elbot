"""Minimal Lavalink playback smoke test."""

from __future__ import annotations

import asyncio
import os

import aiohttp


async def main() -> None:
    host = os.getenv("LAVALINK_HOST", "127.0.0.1")
    port = int(os.getenv("LAVALINK_PORT", "2333"))
    password = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
    base = f"http://{host}:{port}"
    headers = {"Authorization": password}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(
            f"{base}/v4/loadtracks",
            params={"identifier": "ytsearch:lofi hip hop"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        tracks = data.get("data") or []
        if not tracks:
            raise RuntimeError("No tracks returned from Lavalink search")
        encoded = tracks[0].get("encoded")
        if not encoded:
            raise RuntimeError("Track missing encoded payload")

        session_id = "ci-canary"
        await session.patch(
            f"{base}/v4/sessions/{session_id}",
            json={"resuming": False, "timeout": 60},
        )
        await session.patch(
            f"{base}/v4/sessions/{session_id}/players/1",
            json={
                "encodedTrack": encoded,
                "voice": {"token": "ci", "endpoint": "ci.discord.test", "sessionId": "ci"},
            },
        )
        await asyncio.sleep(30)
        await session.delete(f"{base}/v4/sessions/{session_id}/players/1")


if __name__ == "__main__":
    asyncio.run(main())
