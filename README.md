# Elbot

Elbot is a modular Discord bot powered by [Nextcord](https://github.com/nextcord/nextcord) and OpenAI. It includes chat, image generation and music playback features. The project ships with a lightweight management portal and supports running as a system service on Linux and Windows.

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Quick start](#quick-start)
4. [End-to-end platform guides](#end-to-end-platform-guides)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [Running the bot](#running-the-bot)
8. [Running Lavalink](#running-lavalink)
9. [Docker](#docker)
10. [Automated deployment](#automated-deployment)
11. [Windows notes](#windows-notes)
12. [Updating](#updating)
13. [Web Portal](#web-portal)
14. [Architecture overview](#architecture-overview)
15. [Command summary](#command-summary)
16. [Testing](#testing)

## Features

- **Chat** – interact with OpenAI via the `/chat` command. Recent messages are kept briefly to provide context.
- **Chat summaries** – `/chat_summary` provides an OpenAI generated recap of your conversation and chat history now persists across restarts.
- **DALL·E** – generate images using the `/dalle` command.
- **F1** – receive Formula&nbsp;1 schedules, countdowns and race results. Set `ICS_URL` for the calendar feed and `LOCAL_TIMEZONE` for your local zone.
- **Music**: stream audio from YouTube via Lavalink with the actively maintained `dev.lavalink.youtube` plugin and a `yt-dlp` fallback. Requires `ffmpeg` and Java 17. Lavalink downloads and starts automatically. Use the `/play`, `/skip` and `/stop` commands to control playback. The music cog uses Mafic, a maintained Lavalink client for Python.

See `.env.example` for all configuration variables, including `ELBOT_SERVICE` and `PORT` which are used by the management portal.

## Quick start (Linux/macOS)

```bash
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
./infra/scripts/install.sh
```

The installer creates `.venv`, installs dependencies, prompts for required secrets and offers to register the systemd service. After it completes you can either start the foreground bot with:

```bash
source .venv/bin/activate
elbotctl run
```

Or manage the background service:

```bash
elbotctl service start
elbotctl service status
```

Replace `<your-org>` with the account or organisation that hosts your copy of Elbot (for example your GitHub username).

## Quick start (Windows)

```powershell
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
powershell -ExecutionPolicy Bypass -File .\infra\scripts\install.ps1
```

The PowerShell installer mirrors the Linux experience: it builds `.venv`, installs dependencies, collects secrets and registers the Windows service. Activate the virtual environment and launch the bot manually with:

```powershell
.\.venv\Scripts\Activate.ps1
elbotctl run
```

Or control the service directly:

```powershell
elbotctl service status
elbotctl service restart
```

Again, replace `<your-org>` with the account or organisation that hosts the repository.

## End-to-end platform guides

The steps below take you from an empty machine to a running bot on the three mainstream operating systems. They cover installing prerequisites, cloning the repository, configuring credentials and keeping the bot running.

### Linux (Ubuntu/Debian/Fedora)

1. Install prerequisites (update the package names for non-Debian distributions):
   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 python3-venv python3-pip git ffmpeg openjdk-17-jre
   ```
   Fedora/RHEL users can run `sudo dnf install python3 python3-virtualenv git ffmpeg java-17-openjdk`.
2. Clone the repository and enter the project directory:
   ```bash
   git clone https://github.com/<your-org>/Elbot.git
   cd Elbot
   ```
3. Run the guided installer to create the virtual environment, install Python dependencies and prompt for your Discord/OpenAI keys. Add `--yes` for an unattended install:
   ```bash
   ./infra/scripts/install.sh
   ```
4. Review `.env` (created by the script) and fill in any missing values such as `DISCORD_TOKEN`, `OPENAI_API_KEY`, `AUTO_LAVALINK`, or Lavalink credentials if you use a remote node.
5. Start the bot in the foreground for a smoke test:
   ```bash
   source .venv/bin/activate
   elbotctl run
   ```
   Press `Ctrl+C` to stop it.
6. Optional: install the systemd service so the bot and Lavalink start on boot and restart on failure:
   ```bash
   elbotctl service install --require-lavalink
   elbotctl service status
   ```
   Logs are available via `journalctl -u elbot.service`. If you prefer a manual install, copy `infra/systemd/elbot.service`
   to `/etc/systemd/system/`, adjust the paths, run `sudo systemctl daemon-reload` and enable it with
   `sudo systemctl enable --now elbot.service`. A matching `infra/systemd/lavalink.service` template is included for the
   Java node.
7. To update later, rerun `elbotctl update` and restart the service if it does not restart automatically.

### macOS (Intel and Apple silicon)

1. Install prerequisites with Homebrew. If you do not have Homebrew yet, follow the instructions on https://brew.sh first. Then run:
   ```bash
   brew install python@3.12 git ffmpeg openjdk@17
   ```
   Add Java 17 to your shell by appending `export JAVA_HOME="$(/usr/libexec/java_home -v 17)"` to `~/.zshrc` (or `~/.bash_profile`).
2. Clone the repository and switch into it:
   ```bash
   git clone https://github.com/<your-org>/Elbot.git
   cd Elbot
   ```
3. Allow the helper script to create the virtual environment, download dependencies and populate `.env`:
   ```bash
   ./infra/scripts/install.sh
   ```
   When prompted, supply your Discord token and optional OpenAI key.
4. If the script did not prompt you (for example you ran with `--yes`), edit `.env` manually: `cp .env.example .env` then set `DISCORD_TOKEN`, `OPENAI_API_KEY` and any optional values.
5. Start the bot to verify everything works:
   ```bash
   source .venv/bin/activate
   elbotctl run
   ```
6. Optional: install the LaunchAgent so Elbot starts automatically when you log in:
   ```bash
   elbotctl service install
   ```
   This writes `~/Library/LaunchAgents/com.elbot.bot.plist`. Load it immediately with `launchctl load ~/Library/LaunchAgents/com.elbot.bot.plist`. Use `launchctl unload` to remove it.
7. Keep the environment current with `elbotctl update` and restart the LaunchAgent (or rerun step 5) after upgrades.

### Windows 10/11

1. Install prerequisites:
   - [Python 3.12](https://www.python.org/downloads/windows/) with the "Add Python to PATH" option.
   - [Git for Windows](https://git-scm.com/download/win).
   - The Visual Studio Build Tools (Desktop development workload) so Nextcord's optional voice dependencies compile.
   - [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) added to your PATH, or plan to set `FFMPEG_PATH` in `.env`.
2. Launch PowerShell and clone the repository:
   ```powershell
   git clone https://github.com/<your-org>/Elbot.git
   Set-Location Elbot
   ```
3. Run the installer script. It creates `.venv`, installs requirements, collects secrets and offers to register the Windows service:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\infra\scripts\install.ps1
   ```
   Rerun with `-Force` later if you need to reinstall the service.
4. Confirm `.env` contains `DISCORD_TOKEN` and any optional keys (`OPENAI_API_KEY`, Lavalink overrides). Edit it with your favourite editor if you skipped the prompts.
5. Test drive the bot in the current shell:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   elbotctl run
   ```
   Press `Ctrl+C` to stop it.
6. Optional: manage the Windows service so Elbot runs headlessly:
   ```powershell
   elbotctl service status
   elbotctl service restart
   ```
7. When updating, run `elbotctl update`, then restart the service or repeat step 5 for foreground runs.

### Requirements

* Python 3.10+ (3.12 recommended)
* Java 17+ (OpenJDK). On Debian/Ubuntu: `sudo apt-get install openjdk-17-jre`
* ffmpeg in PATH (or set `FFMPEG_PATH` in `.env`)

## Installation

For platform-specific setup steps (Linux/macOS, Windows, Docker) see [INSTALL.md](INSTALL.md).

Quick start:
- Linux/macOS: `./infra/scripts/install.sh`
- Windows: `powershell -ExecutionPolicy Bypass -File .\infra\scripts\install.ps1`
- Docker: `elbotctl docker up`


## Configuration

If you ran `./infra/scripts/install.sh` the script created a `.env` file and asked for
the most important values. Otherwise copy the example and fill in the required
variables:

* `DISCORD_TOKEN` &ndash; your Discord bot token
* `OPENAI_API_KEY` &ndash; your OpenAI API key

Optional variables include `COMMAND_PREFIX`, `GUILD_ID`, `LAVALINK_HOST`,
`LAVALINK_PORT`, `LAVALINK_PASSWORD`, `OPENAI_MODEL`, `ICS_URL`, `F1_CHANNEL_ID`,
`LOCAL_TIMEZONE`, `ELBOT_DATA_DIR`, `ELBOT_SERVICE` and `PORT`.

You can manage these values manually or via the CLI: `elbotctl env list` shows the current configuration, `elbotctl env set KEY value` updates a single entry, and `elbotctl env import file.env` bulk-loads values.

The table below summarises the most common options. Leave entries blank to use
the built-in defaults.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | ✅ | Discord bot token used to authenticate with the gateway. |
| `OPENAI_API_KEY` | ✅ (for chat/image features) | Enables the OpenAI-powered commands such as `/chat` and `/dalle`. |
| `COMMAND_PREFIX` | ❌ | Prefix for legacy text commands when slash commands are unavailable. |
| `AUTO_LAVALINK` | ❌ | Start the bundled Lavalink server automatically (`1` by default). |
| `LAVALINK_HOST` / `LAVALINK_PORT` / `LAVALINK_PASSWORD` | ❌ | Connection details for an external Lavalink server. |
| `FFMPEG_PATH` | ❌ | Path to an ffmpeg executable if it is not already on `PATH`. |
| `YTDLP_COOKIES_FILE` | ❌ | Optional path to a Netscape cookie file passed to yt-dlp for age-restricted videos. |
| `OPENAI_MODEL` | ❌ | Override the OpenAI model name used for chat completions. |
| `ELBOT_DATA_DIR` | ❌ | Custom directory for Lavalink downloads, logs and cached data. |
| `GUILD_ID` | ❌ | Restrict slash commands to a single guild (useful for testing). |
| `ICS_URL` | ❌ | Formula 1 calendar feed URL used by the F1 cog. |
| `F1_CHANNEL_ID` | ❌ | Channel ID that receives automated F1 schedule posts. |
| `LOCAL_TIMEZONE` | ❌ | IANA timezone used for scheduling reminders (defaults to UTC). |
| `ELBOT_SERVICE` | ❌ | Service name that the management portal restarts (default `elbot.service`). |
| `PORT` | ❌ | Listening port for the management portal (defaults to 8000). |
`ICS_URL` may use a `webcal://` address. It will be converted to `https://` automatically.
`LOCAL_TIMEZONE` should be an [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) such as `America/New_York`.
See `.env.example` for an example Formula&nbsp;1 feed and common timezone values.

Common US timezones include:

```
America/New_York
America/Chicago
America/Denver
America/Los_Angeles
America/Phoenix
America/Anchorage
America/Honolulu
```

If you skipped the install script, create the file manually:

```bash
cp .env.example .env
# then edit .env with your values
```

## Running the bot

Activate the virtual environment (if you want the console session) and use the management CLI:

```bash
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
elbotctl run
```

`elbotctl` also exposes convenience commands for day-to-day tasks:

```bash
elbotctl service status    # show systemd/LaunchAgent/Windows service state
elbotctl service restart   # restart the background service
elbotctl check             # validate configuration and Lavalink availability
```

If you prefer Docker, see the next section (`elbotctl docker up`).

## Running Lavalink

Elbot includes an auto-launcher that downloads and starts Lavalink when the
bot runs, selecting a free local port and writing logs to a per-user data
directory (for example `~/.local/share/Elbot/ElbotTeam` on Linux). Set
`ELBOT_DATA_DIR` if you prefer to store the Lavalink files elsewhere. If Java
17+ is not present on the system, Elbot will automatically download a portable
Temurin (Adoptium) JRE 17 to the same directory and use it, so music works out
of the box.

The generated configuration disables Lavalink's deprecated built-in YouTube source and enables the actively maintained `dev.lavalink.youtube` plugin. Override the plugin version with `LAVALINK_YOUTUBE_PLUGIN_VERSION` if you need to pin a release or test a snapshot.

If you swap out the Python client, stick to maintained libraries such as Mafic, Pomice or lavalink.py; Wavelink is currently unmaintained.

Override the port or password with `LAVALINK_PORT` or `LAVALINK_PASSWORD`, or
disable the helper with `AUTO_LAVALINK=0` if you host Lavalink separately.
For example, to run it in Docker:

```bash
docker run -p 2333:2333 \
  -e SERVER_PORT=2333 \
  -e LAVALINK_SERVER_PASSWORD=youshallnotpass \
  ghcr.io/lavalink-devs/lavalink:latest
```

Set `LAVALINK_HOST`, `LAVALINK_PORT` and `LAVALINK_PASSWORD` in your environment
to match your Lavalink instance when auto-launch is disabled.

### YouTube playback configuration

Elbot targets Lavalink v4 together with the maintained
[`dev.lavalink.youtube`](https://github.com/lavalink-devs/youtube-source) plugin. Add the plugin to your `application.yml` (or Docker environment) and disable the legacy source manager to avoid loading the deprecated implementation:

```yaml
lavalink:
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:1.13.5"  # check GitHub for the latest tag
      snapshot: false
  server:
    sources:
      youtube: false
```

After editing the file restart Lavalink. If you use the built-in launcher, set `LAVALINK_YOUTUBE_PLUGIN_VERSION` instead of hand-editing the file. Remote nodes can replace the dependency string above and restart the server; no Python changes are required.

Elbot will fall back to direct stream extraction with `yt-dlp` when Lavalink is
unavailable or throttled. Set the `YT_COOKIES_FILE` (or legacy
`YTDLP_COOKIES_FILE`) environment variable to point at a Netscape cookie file if
you need to access age-restricted videos.

## Docker

Build the container and run the bot and portal with Docker Compose:

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

`elbotctl docker up` wraps the same command if you prefer the CLI.

This starts the bot, management portal and a Lavalink instance for music playback.

The Dockerfile installs `ffmpeg` automatically so music playback works out of the box.

The portal exposes port `8000` by default and can be changed with the `PORT`
environment variable.

## Automated deployment

`infra/scripts/deploy.sh` keeps deployments consistent whether you run it locally or from CI.

- Without deployment variables the script simply runs `docker compose -f infra/docker/docker-compose.yml pull` followed by `docker compose -f infra/docker/docker-compose.yml up -d --build --remove-orphans` on the current machine.
- Set `DEPLOY_HOST`, `DEPLOY_USER` (defaults to `deploy`) and `DEPLOY_PATH` to sync the repository to a remote host over SSH. Provide `SSH_PRIVATE_KEY` and optionally `DEPLOY_INCLUDE_ENV=1` if you want to copy the local `.env`. The script uses `rsync` to mirror the tree (skipping `.env` by default) and restarts the stack using either the Docker Compose plugin or the legacy `docker-compose` binary.
- Set `DEPLOY_COMPOSE_FILE` when the remote deployment should use a compose file other than `docker-compose.yml`.
- Set `SYSTEMD_DEPLOY=1` to run `git pull`, `pip install -r requirements.txt` and
  restart `elbot.service` on the current machine.

The GitHub Actions workflow (`.github/workflows/ci.yml`) reads the same variables from repository secrets. The `Deploy` step only runs for pushes to `main` when `DEPLOY_HOST` is populated, so forks without secrets run the full test suite without touching production.

| Secret | Description |
| --- | --- |
| `DEPLOY_HOST` | Remote hostname or IP address. |
| `DEPLOY_USER` | SSH user (`deploy` by default). |
| `DEPLOY_PATH` | Directory on the remote host that contains the compose file (for example `/opt/elbot`). |
| `DEPLOY_SSH_KEY` | Private key with access to the host. |
| `DEPLOY_INCLUDE_ENV` | Optional (`1`) to sync the repository `.env`; leave unset to keep secrets on the server. |

Ensure the target host has Docker (with the Compose plugin or `docker-compose`) and `rsync` installed; the workflow takes care of the rest.


## Windows notes

- Use `powershell -ExecutionPolicy Bypass -File .\infra\scripts\install.ps1` for a guided setup that installs dependencies and the Windows service.
- Manage the service with `elbotctl service status`, `elbotctl service restart`, or remove it with `elbotctl service remove`.

## macOS notes

`elbot-install-service` installs a LaunchAgent in `~/Library/LaunchAgents/com.elbot.bot.plist` that runs at login
and restarts the bot if it exits.

## Updating

To pull the latest version of Elbot from GitHub:

```bash
elbotctl update
```

To upgrade the YouTube stack:

- Update `yt-dlp` with `pip install -U yt-dlp` (or bump the version in `pyproject.toml`) and restart the bot. You can tweak yt-dlp settings in `elbot/music/cookies.py` if you need custom extraction options.
- Update the Lavalink YouTube plugin by setting `LAVALINK_YOUTUBE_PLUGIN_VERSION` (or editing the dependency in your remote `application.yml`) and restarting Lavalink.

## Web Portal

Elbot includes a lightweight management portal built with Flask. After the
dependencies are installed you can launch it with:

```bash
elbot-portal
```

Open your browser to <http://localhost:8000> by default. The listening port can
be changed with the `PORT` environment variable. The portal lets you view logs,
switch Git branches and run `elbotctl update`. It also provides a button
to restart the bot service.

### PORT

Set `PORT` before launching the portal if you want it to listen on a different
TCP port.

### ELBOT_SERVICE

The portal restarts the bot via `systemctl`. It targets the service
specified by the `ELBOT_SERVICE` environment variable, which defaults to
`elbot.service`. Set this variable before launching the portal if your
systemd unit has a different name.

### AUTO_UPDATE

When `AUTO_UPDATE` is set to `1` the portal spawns a background thread that
runs `elbotctl update` once per day and restarts the bot service after each
update. This allows unattended updates and restarts.

To enable the feature temporarily:

```bash
AUTO_UPDATE=1 elbot-portal
```

Or export the variable in your shell for persistent use.

## Architecture overview

The project is organised into a few key directories:

- **`src/elbot/`** - core package with the main entry point, configuration helper, service installation logic, and the Flask management portal.
- **`src/elbot/cogs/`** - individual modules implementing slash commands for chat, image generation, music playback, and Formula 1 updates.
- **`src/elbot/music/`** - shared music playback components used by the cogs.
- **`infra/scripts/`** - helper shell/PowerShell scripts for install, update, and removal tasks.
- **`infra/docker/`** - Dockerfile, compose stack, and Lavalink configuration.
- **`infra/systemd/`** - systemd unit examples for the bot and Lavalink.
- **`tests/`** - pytest-based test suite.

## Command summary

Elbot exposes several slash commands once invited to your server:

- `/chat`, `/chat_reset` and `/chat_summary` – converse with the bot and view a summary.
- `/dalle` – generate an image from a text prompt.
- Music commands: `/play`, `/skip` and `/stop`.
  You must supply this file yourself because it is not included in the
  repository.
- Formula&nbsp;1 commands: `/f1_schedule`, `/f1_countdown`, `/f1_results`,
  `/f1_subscribe` and `/f1_unsubscribe`.
- Diagnostic commands: `/uptime` and `/ping`.
- Moderation commands: `/kick`, `/ban`, `/clear_messages` and `/clear_bot_messages`.

## Remote development (OpenAI Codex & GitHub Codespaces)

OpenAI Codex and GitHub Codespaces provide lightweight, containerised development environments. They usually lack `systemd` and only expose a shallow Git checkout. Elbot's management portal now handles those constraints gracefully: the branch selector, update status and restart endpoints fall back to informative messages when the required system commands are unavailable instead of raising errors.

To bootstrap either environment quickly:

1. Install the editable package along with the testing extras so Nextcord's voice dependencies and pytest plugins are available.
2. Copy `.env.example` to `.env` and configure the keys you need (for example `DISCORD_TOKEN` and `OPENAI_API_KEY`).
3. Run the automated test suite to verify everything works.

```bash
pip install -e .[test]
pytest
```

When you launch the Flask portal inside Codex or Codespaces, routes that depend on `git` or `systemctl` will return explanatory placeholders instead of failing. You can still inspect logs and run the bot normally.

## Testing

Install Elbot in editable mode so the local package is available and the extras required by the tests (such as `nextcord[voice]`, `aiohttp[speedups]` and `pytest-asyncio`) are installed. These packages are bundled in the `test` extras group:

```bash
pip install -e .[test]
```

Then run the test suite:

```bash
pytest
```

