# Installation Guide

This document walks you through installing, configuring and running Elbot on the three main platforms (Linux/macOS, Windows and Docker). The steps assume you have already cloned the repository.

## 1. Prerequisites

Regardless of platform you need:

- Python 3.10 or newer (3.12 recommended)
- Java 17+ JRE (required for Lavalink)
- `ffmpeg` available on your PATH (or set `FFMPEG_PATH` in `.env`)
- A Discord bot token and optional OpenAI API key if you intend to use chat and image commands

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

Optional exports include `ELBOT_GUILD_ID`, `ELBOT_PORTAL_PORT`,
`ELBOT_LAVALINK_PORT` and `ELBOT_PORTS` if you need to change defaults. The
script requires `apt` and root (or sudo) privileges. Other platforms should use
`./scripts/install.sh`.

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
elbotctl service install --require-lavalink
elbotctl service status
```

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
```

(Use `Remove-Service` only if you want to uninstall the service.)

## 4. Docker

The bundled `infra/docker/docker-compose.yml` file starts the bot, management portal and Lavalink in a single command:

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

Provide configuration via environment variables or bind-mount a `.env` file.

## 5. Configuration

1. Copy `.env.example` to `.env` if the installer did not create one.
2. Fill the required values:
   - `DISCORD_TOKEN`
   - `OPENAI_API_KEY` (optional but required for `/chat` and `/dalle`)
3. Adjust optional settings such as `COMMAND_PREFIX`, `AUTO_LAVALINK`, `ICS_URL`, `LOCAL_TIMEZONE`, and Lavalink credentials if you host your own node.

Whenever you change `.env`, restart the bot.

## 6. Keep the YouTube stack current

- The bundled Lavalink launcher disables the legacy YouTube source and enables the actively maintained `dev.lavalink.youtube` plugin (pinned to `1.13.5`). Override the plugin version with `LAVALINK_YOUTUBE_PLUGIN_VERSION` if you need to pin or test a release, and keep `lavalink.server.sources.youtube` set to `false` in `application.yml` to avoid loading the deprecated source manager.
- `yt-dlp` is used as a fallback resolver. Keep it updated with `pip install -U yt-dlp` whenever YouTube changes break playback.
- If you run a remote Lavalink node, mirror the same plugin configuration in its `application.yml`.

## 7. Running the management portal

Install the package in editable mode (already done by the scripts) and launch:

```bash
elbot-portal
```

By default the portal listens on http://localhost:8000. Set the `PORT` environment variable to change the port. The portal can also restart the bot service when `ELBOT_SERVICE` is set (defaults to `elbot.service`).

## 8. Troubleshooting

- **Voice playback fails** � confirm `ffmpeg` is installed and accessible. Set `FFMPEG_PATH` if it lives outside `PATH`.
- **Lavalink not available** � ensure Java 17 is installed or let `AUTO_LAVALINK=1` download and run the bundled server.
- **Slash commands missing** � invite the bot with the `applications.commands` scope and wait a few minutes for global registration.
- **Permission errors on Linux/macOS** � run the scripts with `sudo` only when prompted to install system packages. The bot itself should run as your regular user.
- **Port 2333 already in use** � check for listeners with `sudo lsof -i :2333` or `sudo ss -tulpn | grep 2333`. Stop stray Lavalink instances with `sudo pkill -f 'java.*Lavalink.jar'` (or `sudo kill $(pgrep -f 'java.*Lavalink.jar')`).

If you run into platform-specific issues, open an issue with details about your OS, Python version and any error logs.

