# CLAUDE.md

Guidance for Claude Code (and other contributors) working in this repo.

## What this is

Elbot is a modular Discord bot (Python, Nextcord) with:

- **Music** — Lavalink (via `mafic`) with a yt-dlp fallback pipeline; Spotify support through the LavaSrc Lavalink plugin. Code in `src/elbot/cogs/music.py` and `src/elbot/music/`.
- **AI chat/images** — OpenAI-backed `/ai` commands (`src/elbot/cogs/ai.py`). Per-channel history is persisted to `chat_history/` at runtime (gitignored).
- **Formula 1** — schedule/countdown/reminder commands (`src/elbot/cogs/F1.py`).
- **Web portal** — Flask app for logs, settings, updates, and branch switching (`src/elbot/portal.py`, `templates/`, `static/`).
- **Ops/self-management** — auto-update jobs, service install helpers, Lavalink lifecycle management (`src/elbot/core/`, `auto_lavalink.py`, `cli.py`).

Entry points (see `pyproject.toml`): `elbot`, `elbot-portal`, `elbotctl`, `elbot-install-service`.

## Dev workflow

- **Run tests:** `python -m pytest` (config in `pytest.ini`; async tests use `pytest-asyncio`)
- **Lint:** `flake8` (config in `.flake8`)
- **CI:** `.github/workflows/ci.yml` (tests) and `lint.yml`
- **Layout:** `src/` layout — package lives in `src/elbot/`, tests in `tests/`, deploy assets in `infra/` (docker, systemd, install scripts).

## Deployment

Production runs on a Raspberry Pi as a systemd service (`elbot.service`, unit template in `infra/systemd/`), executing `.venv/bin/python -m elbot.main` with `Restart=on-failure`. To deploy: `git pull origin main`, reinstall requirements if they changed, then `sudo systemctl restart elbot`. Music runs on Lavalink in one of two modes, chosen in `.env`: the bot starts and manages its own Lavalink process (`AUTO_LAVALINK=1`, port 2333), or with `ELBOT_VOICE_TRANSPORT=bridge` it uses the separate `lavalink-bridge.service` on loopback port 2334 (see `docs/VOICE_BRIDGE_PREREQS.md` and `docs/VOICE_ACCEPTANCE.md`). The standalone `lavalink.service` unit in `infra/systemd/` (config `infra/docker/lavalink/application.yml`) was inactive in the 2026-10-25 audit, so it is not evidence that music is down. Check which mode is configured before diagnosing music.

## Conventions

- Commit messages follow conventional-commit style: `fix(music): ...`, `feat(lavalink): ...`, `chore: ...`, `security: ...`.
- A pre-commit hook (`.githooks/pre-commit`) guards against committing private data; `scripts/check_no_private_data.sh` can be run manually. Never bypass it.
- Secrets live only in `.env` (never committed; `.env.example` documents the variables). Do not `cat` a production `.env` — read specific keys with `grep`.
- Runtime/generated data (`chat_history/`, `output/`, `tmp/`, cookies files) is gitignored — don't commit it.

## Known gotchas

- The Raspberry Pi is slow: Discord's 3-second autocomplete deadline can be exceeded by Lavalink searches, causing intermittent "Loading options failed" on `/play`. This is a timing issue, not a Spotify/LavaSrc config problem. See `PERFORMANCE_TUNING.md`.
- `src/elbot/patch_nextcord.py` patches Nextcord — check it before upgrading the library.
