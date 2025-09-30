# Elbot 🤖

A powerful, modular Discord bot with AI chat, music playback, Formula 1 tracking, and more. Built with [Nextcord](https://github.com/nextcord/nextcord) and designed for easy deployment and management.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/License-MIT-green)

## ✨ Features

- **🎵 Music Playback** - Stream from YouTube via Lavalink with automatic fallback to yt-dlp
- **💬 AI Chat** - OpenAI-powered conversations with context retention and summaries
- **🎨 Image Generation** - Create images using DALL·E 3
- **🏎️ Formula 1** - Track schedules, race results, and get countdown reminders
- **🛡️ Moderation** - Essential tools for server management
- **🌐 Web Portal** - Built-in management interface for configuration and monitoring
- **🔄 Auto Updates** - Keep your bot current with scheduled updates

## 🚀 Quick Start

### One-line install (Linux/macOS)
```bash
git clone https://github.com/<your-org>/Elbot.git && cd Elbot && ./infra/scripts/install.sh
```

### One-line install (Windows)
```powershell
git clone https://github.com/<your-org>/Elbot.git; cd Elbot; .\infra\scripts\install.ps1
```

### Docker
```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

The installer will:
1. ✅ Check prerequisites (Python 3.10+, Java 17+, ffmpeg)
2. ✅ Create virtual environment and install dependencies
3. ✅ Configure your bot with interactive prompts
4. ✅ Optionally install as a system service

### Environment file

Run the guided wizard (`elbotctl install` or the platform script) to copy `.env.example` to `.env` in the project root and fill in your answers.
The packaged systemd service reads secrets from that `.env` via `EnvironmentFile`, so never hard-code tokens directly into a unit file.
If you edit `.env` on Windows, normalize it with `dos2unix .env` before reinstalling the service so the Unix `\n` line endings are preserved.

## 📋 Prerequisites

- **Python** 3.10 or newer (3.12 recommended)
- **Java** 17+ (for Lavalink music server)
- **ffmpeg** (for audio processing)
- **Discord Bot Token** ([Create one here](https://discord.com/developers/applications))
- **OpenAI API Key** (optional, for AI features)

## ⚙️ Configuration

The installer creates a `.env` file with your settings. Key variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Your Discord bot token |
| `OPENAI_API_KEY` | ⭐ | Enables AI chat and image generation |
| `AUTO_LAVALINK` | | Auto-start music server (default: `1`) |
| `AUTO_UPDATE_WEBHOOK` | | Discord webhook notified when scheduled updates fail |
| `ICS_URL` | | F1 calendar feed URL |
| `LOCAL_TIMEZONE` | | Your timezone (e.g., `America/New_York`) |

See [`.env.example`](.env.example) for all options.

For unattended installs export the prefixed variables before running
`elbotctl install --non-interactive` (or the provisioning scripts). The
installer automatically maps them to the `.env` keys:

- `ELBOT_DISCORD_TOKEN` → `DISCORD_TOKEN`
- `ELBOT_OPENAI_KEY` → `OPENAI_API_KEY`
- `ELBOT_LAVALINK_PASSWORD` → `LAVALINK_PASSWORD`
- `ELBOT_LAVALINK_HOST` → `LAVALINK_HOST`
- `ELBOT_USERNAME` → `ELBOT_USERNAME`
- `ELBOT_AUTO_UPDATE_WEBHOOK` → `AUTO_UPDATE_WEBHOOK`

Direct exports such as `DISCORD_TOKEN=...` also work when running the
installer in non-interactive mode.

## 🎮 Bot Commands

### Music 🎵
- `/play <query>` - Play a song or add to queue
- `/skip` - Skip current track
- `/stop` - Stop playback and clear queue
- `/queue` - View current queue
- `/shuffle` - Randomize queue order

### AI Features 🤖
- `/ai chat <message>` - Chat with the AI assistant
- `/ai chat_summary` - Get an AI conversation recap
- `/ai chat_reset` - Clear your chat history
- `/ai image <prompt>` - Generate an image with DALL·E
- `/ai voice` - Experimental voice chat placeholder

### Formula 1 🏎️
- `/f1_schedule` - View upcoming races
- `/f1_countdown` - Time until next session
- `/f1_results` - Latest race results
- `/f1_subscribe` - Get race reminders

### Utilities 🔧
- `/ping` - Check bot latency
- `/uptime` - Bot runtime info
- `/ytcheck` - YouTube playback diagnostics

## 🖥️ Management Portal

Access the web interface to manage your bot:

```bash
elbot-portal  # Default: http://localhost:8000
```

Features:
- Real-time log viewing
- Configuration editor
- Service controls
- Automatic updates scheduler
- Git branch switching

## 🔧 Running the Bot

### Foreground (testing)
```bash
elbotctl run
```

### Background Service
```bash
elbotctl service install --require-lavalink  # Coordinates with Lavalink when available
elbotctl service start
elbotctl service status
```

The installer keeps the strict dependency when a `lavalink.service` unit file is registered so startup ordering still works.
The generated service loads environment variables from `.env`, so rerun the wizard or update the file (and use `dos2unix` if edited on Windows) instead of editing the unit file when tokens change.

### Docker Compose
```bash
elbotctl docker up
elbotctl docker logs --follow
```

## 🔄 Updating

Keep your bot current:

```bash
elbotctl update  # Manual update
```

Or enable automatic daily updates:
1. Open the portal: `elbot-portal`
2. Navigate to the home page.
3. In the *Auto Update Scheduler* card choose **Enable Timer** (systemd) or **Install Cron Job** (cron-capable hosts).
4. Optional: set `AUTO_UPDATE_WEBHOOK` in `.env` so failed runs send a Discord alert.

The scheduled job runs `python -m elbot.core.auto_update_job`, writes to `logs/auto-update.log`, and restarts the bot after successful updates. You can still rely on the legacy background thread by launching the portal with `AUTO_UPDATE=1 elbot-portal`.

## 🏗️ Project Structure

```
Elbot/
├── src/elbot/
│   ├── cogs/        # Bot commands and features
│   ├── music/       # Music playback system
│   ├── core/        # Core utilities
│   └── portal.py    # Web management interface
├── infra/
│   ├── docker/      # Container configurations
│   ├── scripts/     # Installation and deployment
│   └── systemd/     # Service definitions
└── tests/           # Test suite
```

## 🐳 Docker Deployment

The included `docker-compose.yml` provides a complete stack:

```yaml
services:
  lavalink:    # Music server
  bot:         # Discord bot
  portal:      # Management web UI
```

Deploy with:
```bash
docker compose -f infra/docker/docker-compose.yml up -d
```

## 🚀 CI/CD

GitHub Actions automates testing and deployment:

1. Set repository secrets:
   - `DEPLOY_HOST` - Target server
   - `DEPLOY_SSH_KEY` - SSH private key
   - `DEPLOY_PATH` - Remote directory

2. Push to `main` branch to trigger deployment

## 🎵 Music Playback Details

Elbot uses a resilient dual-mode system:
- **Primary**: Lavalink v4 with [`youtube-source`](https://github.com/lavalink-devs/youtube-source) plugin
- **Fallback**: Direct yt-dlp extraction when Lavalink fails

> **Heads-up (Sep 2025):** Auto-Lavalink now defaults to `youtube-source` 1.16.1 because older builds cannot decode YouTube's new SABR signatures. If you pin `LAVALINK_YOUTUBE_PLUGIN_VERSION`, keep it at or above this release to avoid playback failures.

> Keep `yt-dlp` at **2025.9.4** or newer (`pip install --upgrade yt-dlp` inside the venv) so the fallback extractor understands the same signature changes.

> Auto-Lavalink now auto-selects an open port; leave `LAVALINK_PORT` unset or set it to `0` unless you require a fixed value.

### Provide YouTube cookies (recommended)

Elbot's yt-dlp fallback performs best with authenticated cookies. Supplying them unlocks age-restricted content and prevents YouTube `429 Too Many Requests` throttling during long queues.

**Option A - Browser export**
1. Install the "Get cookies.txt LOCALLY" extension ([Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) / [Firefox](https://addons.mozilla.org/firefox/addon/get-cookies-txt-locally/)).
2. Sign in to https://youtube.com in that browser profile.
3. While on youtube.com, click the extension and export `cookies.txt`.
4. Move `cookies.txt` into your Elbot directory (next to `.env`).

**Option B - Command line (works on Pi/servers)**
- Linux / Raspberry Pi (Chromium): `yt-dlp --cookies-from-browser chromium --write-cookies ~/youtube_cookies.txt https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- macOS (Chrome): `yt-dlp --cookies-from-browser chrome --write-cookies ~/youtube_cookies.txt https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- Windows (PowerShell, Chrome): `yt-dlp --cookies-from-browser chrome --write-cookies "$Env:USERPROFILE\youtube_cookies.txt" https://www.youtube.com/watch?v=dQw4w9WgXcQ`
  > Adjust the browser flag (`chromium`, `firefox`, `edge`, ...) if you use something else.

After exporting, set `YT_COOKIES_FILE` in `.env` (or your service config) to the cookie file path and restart Elbot: `elbotctl service restart`.

**Important:** Refresh the cookie export monthly (especially for 24/7 music sessions) so Lavalink and yt-dlp stay authenticated.
## 🧪 Testing

```bash
pip install -e .[test]
pytest
```

## 🆘 Troubleshooting

### Music not playing?
```bash
elbotctl check  # Validates Lavalink connectivity
/ytcheck        # In Discord for diagnostics
```

### Commands not showing?
- Ensure bot has `applications.commands` scope
- Wait up to 60 minutes for global command sync
- Check specific guild with `GUILD_ID` in `.env`

### Port conflicts?
```bash
# Replace 2333 with the port reported in the logs (Lavalink)
sudo lsof -i :2333

# Check port 8000 (Portal)
sudo lsof -i :8000
```

## 📚 Documentation

- [Installation Guide](INSTALL.md) - Detailed setup instructions
- [Discord Setup](DISCORD_SETUP.md) - Bot creation walkthrough
- [API Docs](https://docs.nextcord.dev) - Nextcord documentation

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `pytest`
5. Submit a pull request

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Credits

Built with:
- [Nextcord](https://github.com/nextcord/nextcord) - Discord API wrapper
- [Lavalink](https://github.com/lavalink-devs/Lavalink) - Music streaming server
- [Mafic](https://github.com/ooliver1/mafic) - Lavalink client
- [OpenAI](https://openai.com) - AI capabilities

---

<p align="center">
  Made with ❤️ by the Elbot Team
</p>

