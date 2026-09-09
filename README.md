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
| `ELBOT_MUSIC_DASHBOARD_URL` | Optional public dashboard link on music controls |
| `AUTO_UPDATE_WEBHOOK` | Discord webhook for update failures |
| `ELBOT_PORTAL_SECRET` | Flask session secret |
| `ICS_URL` / `LOCAL_TIMEZONE` | F1 schedule + timezone |

For full options, see [`.env.example`](.env.example).

---

## Commands Overview

### Music

- `/play <query> [play_next]`
- `/skip`, `/stop`, `/pause`, `/resume`, `/seek`, `/volume`, `/nowplaying`
- `/loop`, `/autoplay`, `/clear`, `/disconnect`, `/queue`
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

Default URL: `http://localhost:8000`. The panel binds only to `127.0.0.1` by default.

Before starting the panel, generate an administrator password hash using the project's
Python environment (the password is entered privately at a prompt):

```bash
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Panel password: ')))"
```

Copy the printed hash into `ELBOT_PORTAL_PASSWORD_HASH` in your `.env` or service
environment. Set `ELBOT_PORTAL_USERNAME` (default `admin`) and a persistent random
`ELBOT_PORTAL_SECRET`. Restart the panel after changing these settings. Your browser
will prompt for the administrator username and password. Without a configured hash,
the panel returns HTTP 503 and all pages and actions remain inaccessible.

For remote access, use an SSH tunnel such as `ssh -L 8000:127.0.0.1:8000 user@host`,
then open the local URL. Alternatively, place the panel behind an HTTPS reverse proxy
and a production WSGI server, and set `ELBOT_PORTAL_HTTPS=1`. Keep the upstream private;
HTTP Basic credentials must not travel over an unencrypted remote connection.
`ELBOT_PORTAL_HOST` explicitly overrides the built-in server's bind address; a separate
WSGI server has its own bind setting. Browser POST requests require a session CSRF token,
which the panel forms and log-summary button supply automatically.

The dashboard and authenticated `GET /api/health` read the bot's `logs/health.json`
heartbeat. Run the bot and panel against the same project directory and with access to
that file (normally the same OS account). The bot publishes every 15 seconds; snapshots
older than 45 seconds are stale. Status distinguishes ready, degraded (Discord connected
but no available Lavalink node), disconnected, stale, and unknown. HTTP 200 means ready;
other states return 503. Music health reports Lavalink connectivity, not a playback test
or the availability of the yt-dlp fallback.

Panel commands have deadlines: 30 seconds for Git/status and scheduler commands,
60 seconds for service actions/checks, and 10 minutes for installation/update commands.
On timeout, inspect status before retrying: a spawned child process may still be finishing
work. Automatic updates skip restart on update failure or timeout. Settings saves use
atomic replacement and preserve comments and unrelated values. Unexpected Discord
command errors include a reference ID that can be searched in `logs/elbot.log`.

Portal highlights:

- Update bot and manage auto-update scheduler
- Inspect logs
- Edit environment settings
- Validate Lavalink/yt-dlp health
- Switch git branches

### Running the panel as a non-root service

The panel runs as the bot owner, never root. To let it toggle the update timer and
control the bot service, install the administrator-managed units and a scoped sudoers
rule (edit the user and paths in those files first):

```bash
sudo install -m 0644 infra/systemd/elbot-panel.service infra/systemd/elbot-update.service infra/systemd/elbot-update.timer /etc/systemd/system/
sudo install -m 0440 infra/sudoers.d/elbot-panel /etc/sudoers.d/elbot-panel
sudo visudo -c
sudo systemctl daemon-reload
sudo systemctl enable --now elbot-panel.service elbot-update.timer
```

Then set `ELBOT_PREINSTALLED_TIMER=1` and `ELBOT_SERVICE_SUDO=1` in `.env`. With those
flags the panel uses noninteractive sudo for exactly the six `systemctl` commands in the
sudoers rule and never writes units or runs `daemon-reload` itself. `elbot-update.service`
must run as the bot owner, not root.

Automatic Lavalink ports are resolved from the bot's fresh heartbeat by both panel
diagnostics and CLI validation. A missing or stale heartbeat produces a clear error
instead of connecting to port zero.

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
