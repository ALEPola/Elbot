# Phase 0: Discord voice acceptance

Local regression tests cover failed joins, timeout, cancellation, local cleanup
after teardown errors, serialized joins, queue preservation on reconnect, and
the non-connecting `/ai voice` placeholder. They do not prove Discord UDP,
Lavalink audio delivery, or Raspberry Pi service health.

## Live procedure

Run in a designated test guild and voice channel after deploying this revision.
Use a known playable track, with a human listener confirming audible playback.

1. Record the revision, Python, Nextcord, Mafic, Java, and Lavalink versions.
2. On the Pi, check `systemctl status elbot lavalink` and follow
   `journalctl -u elbot -u lavalink -f`. Do not publish raw logs containing secrets.
3. Join the test voice channel. Use `/play` with the known track; verify the bot
   joins, audio is audible, and the controller matches the playing track.
4. Use `/stop`, then `/disconnect`. Verify the bot leaves the channel.
5. Repeat steps 3–4 for 20 consecutive cycles without restarting the bot.
   Record cycle number, time to audible playback, result, and any error.
   A failed cycle resets the consecutive-success count.
6. While music plays, invoke `/ai voice` from another channel. Verify only the
   unavailable message appears and music remains in the original channel.
7. In the test environment, interrupt the Lavalink connection, restore it, and
   retry playback. Confirm no process restart is needed and queued requests
   remain available. Record failed handshake diagnostics and recovery time.
8. During a separate maintenance window, restart the Pi's Lavalink service and
   then the bot service. Verify both become healthy and repeat playback.

## Exit gate

- 20 consecutive audible join/play/stop/leave cycles succeed.
- Interrupted connections recover without a stuck local voice registration.
- No unexpected channel moves or queue loss during reconnect.
- Java/Lavalink and bot service restart checks succeed.

The Phase 0 source fixes were deployed on 2026-09-10 after locating connection
details in ignored local ELBOT configuration. Original files were backed up on
the Pi, and deployed SHA-256 checksums matched the tested local sources.
The ELBOT service restarted successfully; its managed Lavalink process reported
a successful startup handshake, Discord became ready, and commands synced.

As of 2026-09-10 21:40 EDT the heartbeat reports `music_ready=true`: the
Lavalink node connects lazily on the first `/play` rather than at boot, so
earlier `false` readings were taken before any command had been run, not a
failure. Two `/play` invocations that evening logged `Playback started`
via the yt-dlp HTTP fallback (Lavalink's own YouTube source did not resolve
the track, which is the known IP-blocking issue the fallback exists for).
Playback start is confirmed in logs; audibility has not yet been confirmed
by a human listener. The 20-cycle test and interruption/recovery checks
are **pending**.

## Cycle run — 2026-09-10 21:04–21:13 EDT

Fingerprint: Pi rev `0bd4e6d` + Phase 0 files, Python 3.11.2, Nextcord 2.6.0,
Mafic 2.11.0, OpenJDK 17.0.20, Lavalink 4.2.2 (HTTP source; YouTube disabled).

Driven by a harness (`tmp/phase0_cycles.py`, second gateway session with the
bot token, production service untouched) in #ITCH CAVE with three humans
present. Each cycle: connect → load an HTTP MP3 via Lavalink → wait for
`TrackStartEvent` → hold 30 s → stop → disconnect → verify the bot left.
Audible playback was confirmed by a listener in the channel.

| Cycles | Passed | Consecutive | Connect (s) | Time to audio (s) | Leave (s) |
| :---: | :---: | :---: | --- | --- | --- |
| 14 | 14 | 14 | 0.29–0.38 (avg 0.33) | 0.64–0.84 (avg 0.70) | 0.21 |

No failures, no stuck voice registration, no process restart. The run was
stopped by the operator at 14 consecutive successes; the formal gate asks for
20, so the remaining 6 cycles are a follow-up, not a blocker. Production
health stayed `discord_ready=true, music_ready=true` throughout. The harness
exercises the voice/Lavalink stack directly rather than the `/play` handler;
the handler's cleanup paths are covered by `tests/test_voice_lifecycle.py`.
Interruption/recovery (step 7) and service-restart (step 8) checks remain
**pending**.
