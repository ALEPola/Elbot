# Elbot 🤖

A modular Discord bot with music playback, AI chat, Formula 1 utilities, and a built-in management portal.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![CI](https://img.shields.io/github/actions/workflow/status/<your-org>/Elbot/ci.yml?label=ci)

## Why Elbot?

- 🎵 **Reliable music** with Lavalink + yt-dlp fallback
- 💬 **AI assistant tools** for chat summaries and image generation
- 🏎️ **F1 commands** for schedule, countdowns, and reminders
- 🌐 **Web portal** for logs, settings, updates, and branch switching
- 🔄 **Automated maintenance** via update jobs and service helpers

---

## Quick Start

### Linux / macOS

```bash
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
./infra/scripts/install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
.\infra\scripts\install.ps1
```

### Docker

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

The install script checks prerequisites, creates the virtual environment, helps generate your `.env`, and can install service units.

---

## Requirements

- Python **3.10+** (3.12 recommended)
- Java **17+** (for Lavalink)
- `ffmpeg`
- Discord bot token
- Optional OpenAI API key

---

## Configuration

Use `elbotctl install` (or platform install scripts) to generate `.env`.

Key variables:

| Variable | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Discord bot token |
| `OPENAI_API_KEY` | Enables AI features |
| `OPENAI_MODEL` | OpenAI model override |
| `COMMAND_PREFIX` | Legacy text command prefix |
| `LAVALINK_HOST` / `LAVALINK_PORT` / `LAVALINK_PASSWORD` | Music backend config |
| `AUTO_LAVALINK` | Auto-manage Lavalink lifecycle |
| `YT_COOKIES_FILE` | Cookies for improved YouTube reliability |
| `AUTO_UPDATE_WEBHOOK` | Discord webhook for update failures |
| `ELBOT_PORTAL_SECRET` | Flask session secret |
| `ICS_URL` / `LOCAL_TIMEZONE` | F1 schedule + timezone |

For full options, see [`.env.example`](.env.example).

---

## Commands Overview

### Music

- `/play <query> [play_next]`
- `/skip`, `/stop`, `/queue`
- `/remove <index|start-end>`, `/move <source> <destination>`, `/shuffle`, `/replay`

### AI

- `/ai chat <message>`
- `/ai chat_summary`, `/ai chat_reset`
- `/ai image <prompt>`
- `/ai voice`, `/ai voice_toggle <enabled>`

### Formula 1

- `/f1_schedule`, `/f1_countdown`, `/f1_results`
- `/f1_subscribe`, `/f1_unsubscribe`

### Utility / Admin

- `/ping`, `/uptime`, `/ytcheck`, `/musicdebug`
- `/kick`, `/ban`, `/clear_messages`, `/clear_bot_messages`

---

## Management Portal

Start the portal:

```bash
elbot-portal
```

Default URL: `http://localhost:8000`

Portal highlights:

- Update bot and manage auto-update scheduler
- Inspect logs
- Edit environment settings
- Validate Lavalink/yt-dlp health
- Switch git branches

---

## Running Elbot

### Local foreground

```bash
elbotctl run
```

### Service mode

```bash
elbotctl service install --require-lavalink
elbotctl service start
elbotctl service status
```

### Docker helpers

```bash
elbotctl docker up
elbotctl docker logs --follow
```

---

## Updating

Manual update:

```bash
elbotctl update
```

Automated daily updates can be enabled in the portal’s **Auto Update Scheduler** card.

---

## Project Layout

```text
Elbot/
├── src/elbot/
│   ├── cogs/       # Discord commands/features
│   ├── core/       # Update/runtime/service helpers
│   ├── music/      # Playback + queue + cookies
│   ├── templates/  # Portal pages
│   └── portal.py   # Flask portal app
├── infra/
│   ├── docker/
│   ├── scripts/
│   └── systemd/
└── tests/
```

---

## CI

GitHub Actions workflows live in [`.github/workflows`](.github/workflows) and run test/lint pipelines.

---

## Security Notes

- Keep secrets in `.env`, not in service unit files.
- Never commit tokens, cookies, or private keys.
- Run `./scripts/check_no_private_data.sh` before pushing changes.

---

## License

[MIT](LICENSE)
