"""Embed helpers for music interactions."""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Sequence

import nextcord

from .queue import QueuedTrack

__all__ = ["EmbedFactory", "QueuePaginator"]


def _format_duration(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    return str(dt.timedelta(seconds=seconds))


def _format_eta(ms: int) -> str:
    if ms <= 0:
        return "Ready"
    seconds = int(ms // 1000)
    return f"{seconds // 60}m {seconds % 60}s"


class EmbedFactory:
    """Create embeds for playback events."""

    def __init__(self, *, color: int = 0x5865F2) -> None:
        self.color = color

    def now_playing(self, track: QueuedTrack, *, position: int = 0, eta_ms: int = 0) -> nextcord.Embed:
        info = track.handle
        title = info.title or track.query or "Unknown title"
        if title.lower().startswith("unknown") and track.query:
            title = track.query
        author = info.author or track.fallback_source or "Unknown"
        embed = nextcord.Embed(title="Now Playing", color=self.color)
        embed.description = f"[{title}]({info.uri or track.query})"
        embed.add_field(name="Channel", value=author, inline=True)
        embed.add_field(name="Duration", value=_format_duration(info.duration), inline=True)
        embed.add_field(name="Requested by", value=track.requester_display, inline=True)
        embed.add_field(name="Queue position", value=str(position), inline=True)
        embed.add_field(name="ETA", value=_format_eta(eta_ms), inline=True)
        embed.set_footer(text="Fallback" if track.is_fallback else "Lavalink")
        return embed

    def queued(self, track: QueuedTrack, *, position: int, eta_ms: int) -> nextcord.Embed:
        info = track.handle
        title = info.title or track.query or "Unknown title"
        if title.lower().startswith("unknown") and track.query:
            title = track.query
        author = info.author or track.fallback_source or "Unknown"
        embed = nextcord.Embed(title="Track queued", color=self.color)
        embed.description = f"[{title}]({info.uri or track.query})"
        embed.add_field(name="Channel", value=author, inline=True)
        embed.add_field(name="Duration", value=_format_duration(info.duration), inline=True)
        embed.add_field(name="Queue position", value=str(position), inline=True)
        embed.add_field(name="Estimated time", value=_format_eta(eta_ms), inline=True)
        embed.set_footer(text=f"Requested by {track.requester_display}")
        return embed

    def queue_page(
        self,
        tracks: Sequence[QueuedTrack],
        *,
        page: int,
        per_page: int,
        total: int,
        now_playing: Optional[QueuedTrack] = None,
    ) -> nextcord.Embed:
        embed = nextcord.Embed(title="Queue", color=self.color)
        if now_playing:
            np_handle = now_playing.handle
            np_title = np_handle.title or now_playing.query or "Unknown title"
            if np_title.lower().startswith("unknown") and now_playing.query:
                np_title = now_playing.query
            embed.add_field(
                name="Now Playing",
                value=f"[{np_title}]({np_handle.uri or now_playing.query})",
                inline=False,
            )
        if not tracks:
            embed.description = "Queue is empty."
        else:
            lines: List[str] = []
            for index, track in enumerate(tracks, start=1 + page * per_page):
                info = track.handle
                title = info.title or track.query or "Unknown title"
                if title.lower().startswith("unknown") and track.query:
                    title = track.query
                duration = _format_duration(info.duration)
                lines.append(f"`{index}.` [{title}]({info.uri or track.query}) - {duration}")
            embed.description = "\n".join(lines)
        embed.set_footer(text=f"Page {page + 1}/{max(1, (total + per_page - 1) // per_page)}")
        return embed


    def failure(self, message: str) -> nextcord.Embed:
        return nextcord.Embed(title="Playback failed", description=message, color=0xFF5555)


class QueuePaginator(nextcord.ui.View):
    """Simple button-based pagination for queue embeds."""

    def __init__(
        self,
        factory: EmbedFactory,
        tracks: Sequence[QueuedTrack],
        *,
        per_page: int = 8,
        now_playing: Optional[QueuedTrack] = None,
    ) -> None:
        super().__init__(timeout=60)
        self.factory = factory
        self.tracks = list(tracks)
        self.per_page = per_page
        self.now_playing = now_playing
        self.page = 0
        self.message: Optional[nextcord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.first_button.disabled = self.page == 0
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.last_button.disabled = self.page >= total_pages - 1

    def _current_slice(self) -> Sequence[QueuedTrack]:
        start = self.page * self.per_page
        end = start + self.per_page
        return self.tracks[start:end]

    async def send_initial(self, interaction: nextcord.Interaction) -> None:
        embed = self.factory.queue_page(
            self._current_slice(),
            page=self.page,
            per_page=self.per_page,
            total=len(self.tracks),
            now_playing=self.now_playing,
        )
        if interaction.response.is_done():
            self.message = await interaction.followup.send(embed=embed, view=self)
        else:
            self.message = await interaction.send(embed=embed, view=self)

    async def update_message(self) -> None:
        if not self.message:
            return
        embed = self.factory.queue_page(
            self._current_slice(),
            page=self.page,
            per_page=self.per_page,
            total=len(self.tracks),
            now_playing=self.now_playing,
        )
        await self.message.edit(embed=embed, view=self)

    @nextcord.ui.button(label="≪", style=nextcord.ButtonStyle.secondary)
    async def first_button(self, _: nextcord.ui.Button, interaction: nextcord.Interaction) -> None:
        self.page = 0
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="‹", style=nextcord.ButtonStyle.secondary)
    async def prev_button(self, _: nextcord.ui.Button, interaction: nextcord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="›", style=nextcord.ButtonStyle.secondary)
    async def next_button(self, _: nextcord.ui.Button, interaction: nextcord.Interaction) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.page = min(total_pages - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

    @nextcord.ui.button(label="≫", style=nextcord.ButtonStyle.secondary)
    async def last_button(self, _: nextcord.ui.Button, interaction: nextcord.Interaction) -> None:
        total_pages = max(1, (len(self.tracks) + self.per_page - 1) // self.per_page)
        self.page = total_pages - 1
        self._update_buttons()
        await interaction.response.defer()
        await self.update_message()

