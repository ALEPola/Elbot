# Voice bridge prerequisites (Phase 1 infra)

Research record for enabling `ELBOT_VOICE_TRANSPORT=bridge` on the Pi.
Verified 2026-09-10 against the live Pi and the public fork. Nothing here has
been deployed.

## 1. The Lavalink fork is real, small, and matches our code

- Repo: `Aiko-IT-Systems/Lavalink`, branch `feat/transport-bridge`, head
  `e72ce9ae51be7fd216a6664fcfe623ed5d446e5c` (2026-04-21, Lala Sabathil).
  This is exactly the commit pinned in `frames.mjs`.
- Five commits, ~550 added lines: a `VoiceTransport` abstraction with
  `KoeVoiceTransport` (stock behaviour) and `ExternalBridgeTransport`, a
  `/bridge/v1` WebSocket server, a Bearer-token handshake interceptor, a
  bridge Dockerfile and a GHCR workflow. Five commits behind upstream master.
- Not upstreamed: no matching PR in `lavalink-devs/Lavalink`. Last activity
  April 2026. Treat it as a maintained-by-one-person patch, not a product.
- Wire format confirmed from source: 20-byte header = version(1) `0x01`,
  packet type(1) `0x00` = Opus, guild id (u64 BE), sequence (u32), timestamp
  (u32), duration ms (u16 = 20), then raw Opus. `frames.mjs` validates
  exactly these fields. Auth: `Authorization: Bearer <token>` on
  `/bridge/v1`, else HTTP 401. Config:

  ```yaml
  lavalink:
    server:
      transport-mode: external_bridge   # default "koe"
      bridge:
        auth-token: "<ELBOT_VOICE_BRIDGE_TOKEN>"
  ```

- Behaviour to know: the transport is send-only (Lavalink exports Opus; it
  never receives audio — receive is entirely the Node side), and it has no
  retry or fallback if the bridge client drops. Voice-gateway handling is
  delegated to the bridge, which in our design is fed by Nextcord.

## 2. Transport mode is server-wide, not per-player

`transport-mode` is a `@ConditionalOnProperty` switch on the whole server:
a Lavalink in `external_bridge` mode cannot do normal Koe playback. Since
`BridgePlayer` defaults its bridge URL to the same host/port Mafic uses,
enabling the bridge means **all** voice for the bot goes through
Node/`@discordjs/voice`, including ordinary music. Consequences:

- It is an all-or-nothing flag per bot process, which the current
  `ELBOT_VOICE_TRANSPORT` design already reflects.
- Phase 0's voice reliability result (14/14 cycles) was measured on the Koe
  path. The bridge path is a different voice stack and needs its own cycle
  run before it carries music for real users.
- Running both modes side by side needs two Lavalink instances on different
  ports, and `ELBOT_VOICE_BRIDGE_URL` pointed at the bridge one.

## 3. Building the fork

- No releases, and the GHCR workflow builds `linux/amd64` only, so the
  published image (if any) cannot run on the Pi.
- Build requirement is Java 17 (`sourceCompatibility = VERSION_17`); the
  `bootJar` task produces `Lavalink.jar`, which is architecture-independent.
  The Pi already runs plain `Lavalink.jar` 4.2.2 on OpenJDK 17 armhf.
- Cheapest path: build once on the Windows dev machine (Temurin JDK 17 is
  installed; the repo ships the Gradle wrapper), copy `Lavalink.jar` to the
  Pi. Do not build on the Pi: 5 GB free disk, and the Gradle cache alone is
  ~2 GB.
- Docker is on the Pi (28.5.2, user in the `docker` group) but already hosts
  Immich; adding a JVM container there is possible but not the light option.

## 4. Node 22 on the Pi

- Pi is Raspbian 12 bookworm, 64-bit kernel, **32-bit armhf userland**,
  Node 18.20.8 from NodeSource's `node_18.x` armhf channel. NodeSource does
  not publish armhf builds for Node 20+, so `apt` cannot upgrade it.
- nodejs.org ships official `linux-armv7l` tarballs for v22 (22.23.2 at time
  of writing). Install to e.g. `/opt/node22` and set
  `ELBOT_VOICE_NODE=/opt/node22/bin/node`; system Node 18 stays untouched.
- Native deps check out for armhf: `@snazzah/davey` (DAVE E2EE) ships
  `linux-arm-gnueabihf` and `wasm32-wasi` builds; `opusscript` is WASM;
  `ws` and `prism-media` are pure JS. `@discordjs/voice` 0.19 can use
  Node's built-in AES-GCM, so no sodium package is required.
- The lockfile is pnpm's; use `corepack pnpm install --frozen-lockfile`
  (corepack is bundled with Node 22). Expect ~60 MB of `node_modules`.

## 5. Suggested order when greenlit

1. Build the fork jar on Windows; keep the checksum.
2. Install Node 22 to `/opt/node22`; `pnpm install` in
   `src/elbot/live/transport`; run `node --test` there.
3. Run the fork on a second port (`transport-mode: external_bridge`) with
   the stock instance untouched; set `ELBOT_VOICE_BRIDGE_URL`,
   `ELBOT_VOICE_BRIDGE_TOKEN`, `ELBOT_VOICE_NODE`.
4. Flip `ELBOT_VOICE_TRANSPORT=bridge` on a test run only; repeat the Phase 0
   cycle harness against the bridge path; then `/listen start` for the
   Phase 1 exit test (speaker start/stop with correct display names).
5. Only then decide whether the bridge instance replaces the stock one.
