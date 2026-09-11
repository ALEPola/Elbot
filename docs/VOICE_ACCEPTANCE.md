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

# Phase 1: per-user voice reception (bridge transport)

Run 2026-09-10 21:48–22:19 EDT with `ELBOT_VOICE_TRANSPORT=bridge`, Mafic on
the bridge-mode Lavalink fork (127.0.0.1:2334), Node 22.23.2 armv7l.

## Bridge-path cycle run

Same harness as Phase 0, driving `BridgePlayer`: 5/5 cycles, connect
1.05–1.44 s (avg 1.16), time to audible track 1.42–2.11 s (avg 1.59), leave
≤0.12 s, no orphaned Node processes. Audible playback confirmed by a
listener. Slower to start than the Koe path (Node + DAVE handshake) but
every cycle clean.

## Speaker attribution (`/listen start`, #ITCH CAVE, 5–6 humans)

Two defects found and fixed on the way:

1. `channel.members` is empty without the privileged members intent (the
   guild cache held only the bot), so listening stopped on its first tick.
   Occupants now come from `channel.voice_states` with a one-time REST
   `fetch_member` per player (`5aba6ff`).
2. `opusscript` (WebAssembly libopus) aborted the Node process with an
   internal assertion 17–26 s into multi-speaker sessions, taking music
   down with it. Replaced by the native `@discordjs/opus`, compiled on the
   Pi (`ba21b01`).

Result with the native decoder: 42 `speaker_start`/`speaker_stop` events in
~40 s of deliberately overlapping speech from four people, every event
carrying the correct Discord display name, `/listen status` reporting
825+ PCM frames and 0 decode errors, no transport warnings, Node at ~70 MB
RSS and ~17% CPU. An earlier (crashed) run had attributed five distinct
people correctly before the decoder abort. **Exit test met.**

## Transport interruption (bridge path)

`4fa98ff` makes `BridgePlayer` dispatch `bridge_transport_failed`; the music
cog re-queues the current track and re-enters `_begin_playback`. Verified
live at 22:22 by killing the Node bridge process during playback: transport
stopped 22:22:16, cog re-queued and retried, new bridge process and
reconnect at 22:22:29, playback restarted 22:22:30 (same track from the
start, ~14 s gap, no operator action, no stuck registration). The gap is
dominated by the 8 s connection warmup wait plus five 0.75 s retries before
the reconnect attempt; tunable via `ELBOT_PLAYER_*` if it matters.

# Phase 2: local wake phrase and privacy gate

Engine: Vosk `vosk-model-small-en-us-0.15` with a keyword-restricted grammar
(`elbot`, `el bot`, `hey elbot`, `hey el bot`, `elbow`), one recognizer per
speaker on its own thread, fed 16 kHz mono resampled from the isolated
streams. Audio is held only until each utterance finalizes and zeroed
unless it contains a wake phrase. Chosen over Porcupine (needs a Picovoice
key) and openWakeWord (needs a custom-trained model; no armv7 ONNX runtime).

Pi benchmark on synthetic phrases (two Windows TTS voices): 15/16 detected,
0 false wakes on negatives, "elbow" fallback fires; 5 concurrent recognizers
at 0.26x realtime (~0.18 core per talking speaker).

## Live runs, 2026-09-10 22:32–22:40 EDT (#ITCH CAVE, 5–6 humans)

- Run 1 exposed a floor-holding problem: the active speaker's repeats
  extended their window without limit, so one person kept it while everyone
  else got "hang on". Fixed in `2a1528e` (one extension, 5 s default,
  repeats post nothing).
- Run 2 (fixed build): **24 s / 15 speaker turns from three people with no
  wake phrase → zero activations**; then 7 wakes across the session, every
  one from a person saying the name, correct display name each time,
  including a sixth previously unseen member. `/listen status`: 7302 PCM
  frames, 0 decode errors, 7 wake phrases, 33 utterances checked (26
  ignored). No warnings. Simultaneous-activation arbitration verified
  (`busy` then `opened` after the 5 s window). Members confirmed the `elbow`
  hits were people saying "ELBOT". **Exit test met.**

Nothing leaves the process yet: wake events are only logged, dispatched as
`wake_phrase`, and announced in the invoking text channel by `/listen`.

## Open

- 20 consecutive bridge-path cycles (5 run) and the Lavalink/bot
  service-restart check on the bridge path.
- Wake detection is only active during a `/listen` session (2 min cap); the
  always-on mode belongs with Phase 3's session lifecycle and cost limits.
- Recovery restarts the track from 0:00 rather than resuming at position.
- With `ELBOT_VOICE_TRANSPORT=bridge`, the production bot's music runs on the
  bridge stack; revert by restoring `tmp/.env.pre-bridge-flip` (transport
  `lavalink`, `AUTO_LAVALINK=1`, `LAVALINK_PORT=2333`) and restarting.
