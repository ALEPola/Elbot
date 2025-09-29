# Installation Guide

This document walks you through installing, configuring and running Elbot on the three main platforms (Linux/macOS, Windows and Docker). The steps assume you have already cloned the repository.

## 1. Prerequisites

Regardless of platform you need:

- Python 3.10 or newer (3.12 recommended)
- Java 17+ JRE (required for Lavalink)
- `ffmpeg` available on your PATH (or set `FFMPEG_PATH` in `.env`)
- A Discord bot token and optional OpenAI API key if you intend to use chat and image commands
- (Recommended) A fresh YouTube cookies export if you plan to run large queues or 24/7 playback—configure `YT_COOKIES_FILE` to avoid `429` throttling. Follow the [yt-dlp cookies guide](https://github.com/yt-dlp/yt-dlp/wiki/How-to-use-your-own-YouTube-Cookies) to create the file.

Platform-specific notes:

- **Linux/macOS** � install build tools (e.g. `build-essential`, `libffi-dev`, `python3-dev`) when prompted.
- **Windows** � ensure the "Desktop development with C++" workload or the stand-alone Build Tools are installed for compiling voice dependencies.
- **Docker** � Docker Desktop or a compatible engine.

## 2. Linux & macOS

### Quick start

```bash
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
./infra/scripts/run.sh
```

`install.sh` performs the first-time setup: it creates a virtual environment, installs dependencies and prompts for your Discord token. Rerun the script with `--yes` for unattended installs or use `elbotctl` commands afterwards to manage the bot.`

### Unattended bootstrap (Ubuntu/Debian)

On Debian/Ubuntu you can let the provisioning script install all prerequisites,
configure `.env`, free the default ports and execute the guided installer in
non-interactive mode. Provide the required secrets via environment variables:

```bash
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
ELBOT_DISCORD_TOKEN="your-token" \
ELBOT_OPENAI_KEY="sk-..." \
./scripts/provision.sh
```

Optional exports include the following if you want to bypass prompts or change
defaults while remaining non-interactive:

- `ELBOT_LAVALINK_PASSWORD`
- `ELBOT_LAVALINK_HOST`
- `ELBOT_USERNAME`
- `ELBOT_AUTO_UPDATE_WEBHOOK`
- `ELBOT_GUILD_ID`, `ELBOT_PORTAL_PORT`, `ELBOT_LAVALINK_PORT`, `ELBOT_PORTS`

The script requires `apt` and root (or sudo) privileges. Other platforms should
use `./scripts/install.sh`.

### Guided install script

Use the helper script to install system packages (Java, ffmpeg), create the virtual environment and write a `.env` file:

```bash
./infra/scripts/install.sh
```

Add `--yes` to accept all prompts automatically. After the script finishes you can start the bot with:

```bash
source .venv/bin/activate
python -m elbot.main
```

To install or manage the service later use:

```bash
elbotctl service install --require-lavalink  # Falls back gracefully if Lavalink isn't registered yet
elbotctl service status
elbotctl auto-update enable  # Optional scheduler helper
elbotctl auto-update status
```

Or call the unified helper directly:

```bash
python -m elbot.core.deploy service install
python -m elbot.core.deploy auto-update enable
```

When a Lavalink unit file is present the installer still wires Elbot to require it so ordering guarantees remain intact.

Run `elbotctl service remove` to uninstall it.

## 3. Windows

### Quick start (PowerShell)

```powershell
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
.\infra\scripts\install.ps1
```

The script creates a virtual environment, installs dependencies, prompts for required secrets and offers to register the Windows service. Once installed the "Elbot" service will start automatically on boot.

To run the bot manually after setup:

```powershell
.\.venv\Scripts\Activate.ps1
elbotctl run
```

You can control the Windows service with:

```powershell
elbotctl service status
elbotctl service restart
elbotctl auto-update enable
elbotctl auto-update status
```

(Use `Remove-Service` only if you want to uninstall the service.)

## 4. Docker

The bundled `infra/docker/docker-compose.yml` file starts the bot, management portal and Lavalink in a single command:

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

Provide configuration via environment variables or bind-mount a `.env` file.

## 5. Configuration

- Run the guided wizard (`./infra/scripts/install.sh` or `elbotctl install`) so it copies `.env.example` to `.env` in the project root and records your answers.
- The packaged `elbot.service` unit reads secrets from `.env` via `EnvironmentFile`, so never hard-code tokens inside the unit file itself.
- If you edit `.env` on Windows, run `dos2unix .env` (or an equivalent) before reinstalling the service so the file keeps Unix `\n` newlines.
- Ensure the required values are present:
  - `DISCORD_TOKEN`
  - `OPENAI_API_KEY` (optional but required for `/chat` and `/dalle`)
- Adjust optional settings such as `COMMAND_PREFIX`, `AUTO_LAVALINK`, `ICS_URL`, `LOCAL_TIMEZONE`, and Lavalink credentials if you host your own node.

Whenever you change `.env`, restart the bot.

## 6. Keep the YouTube stack current

- The bundled Lavalink launcher disables the legacy YouTube source and enables the actively maintained `dev.lavalink.youtube` plugin (pinned to `1.16.1` by default). Override the plugin version with `LAVALINK_YOUTUBE_PLUGIN_VERSION` if you need to pin or test a release, and keep `lavalink.server.sources.youtube` set to `false` in `application.yml` to avoid loading the deprecated source manager.
- Leave `LAVALINK_PORT` unset or set it to `0` to let auto-lavalink choose the first available local port. Provide a concrete value only if you
  need to expose Lavalink on a fixed port (for remote nodes or firewall rules).
- `yt-dlp` is used as a fallback resolver. Keep it at `2025.9.4` or newer (`pip install --upgrade yt-dlp`) whenever YouTube changes break playback; the installer enforces this minimum.
- If you run a remote Lavalink node, mirror the same plugin configuration in its `application.yml`.

## 7. Running the management portal

Install the package in editable mode (already done by the scripts) and launch:

```bash
elbot-portal
```

By default the portal listens on http://localhost:8000. Set the `PORT` environment variable to change the port. The portal can also restart the bot service when `ELBOT_SERVICE` is set (defaults to `elbot.service`).

## 8. Troubleshooting

- **Voice playback fails** – confirm `ffmpeg` is installed and accessible. Set `FFMPEG_PATH` if it lives outside `PATH`.
- **Lavalink not available** – ensure Java 17 is installed or let `AUTO_LAVALINK=1` download and run the bundled server.
- **Slash commands missing** – invite the bot with the `applications.commands` scope and wait a few minutes for global registration.
- **Permission errors on Linux/macOS** – run the scripts with `sudo` only when prompted to install system packages. The bot itself should run as your regular user.
- **Lavalink port already in use** – use the port reported in the logs (2333 by default) with `sudo lsof -i :<port>` or `sudo ss -tulpn | grep <port>`. Stop stray Lavalink instances with `sudo pkill -f 'java.*Lavalink.jar'` (or `sudo kill $(pgrep -f 'java.*Lavalink.jar')`).
- **Frequent YouTube 429 errors on long sessions** – export fresh cookies from a logged-in browser profile and set `YT_COOKIES_FILE` so Lavalink and yt-dlp reuse an authenticated session instead of anonymous scraping.

If you run into platform-specific issues, open an issue with details about your OS, Python version and any error logs.

