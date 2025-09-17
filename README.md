# Elbot

Elbot is a modular Discord bot powered by [Nextcord](https://github.com/nextcord/nextcord) and OpenAI. It includes chat, image generation and music playback features. The project ships with a lightweight management portal and supports running as a system service on Linux and Windows.

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Quick start](#quick-start)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the bot](#running-the-bot)
7. [Running Lavalink](#running-lavalink)
8. [Docker](#docker)
9. [Windows notes](#windows-notes)
10. [Updating](#updating)
11. [Web Portal](#web-portal)
12. [Architecture overview](#architecture-overview)
13. [Command summary](#command-summary)
14. [Testing](#testing)

## Features

- **Chat** – interact with OpenAI via the `/chat` command. Recent messages are kept briefly to provide context.
- **Chat summaries** – `/chat_summary` provides an OpenAI generated recap of your conversation and chat history now persists across restarts.
- **DALL·E** – generate images using the `/dalle` command.
- **F1** – receive Formula&nbsp;1 schedules, countdowns and race results. Set `ICS_URL` for the calendar feed and `LOCAL_TIMEZONE` for your local zone.
- **Music** – stream audio from YouTube via a Lavalink server. Requires
  `ffmpeg` installed and Java 17. Lavalink downloads and starts automatically.
  Use the `/play`, `/skip` and `/stop` commands to control playback.
- **Diagnostic** – utility commands for bot admins.
- **Moderation** – `/kick`, `/ban`, `/clear_messages` and `/clear_bot_messages` commands for server admins.
- **Portal auto-update** – the Flask portal can report update status and optionally run daily updates.

See `.env.example` for all configuration variables, including `ELBOT_SERVICE` and `PORT` which are used by the management portal.

## Quick start (Linux/macOS)

```bash
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
./scripts/run.sh
# then edit .env and paste your DISCORD_TOKEN, run again
```

Replace `<your-org>` with the account or organisation that hosts your copy of
Elbot (for example your GitHub username).

## Quick start (Windows)

```powershell
git clone https://github.com/<your-org>/Elbot.git
cd Elbot
# One-time setup (creates venv, installs deps, prompts for token, installs service)
./scripts/install.ps1
# The "Elbot" Windows service will start automatically on boot.
```

Again, replace `<your-org>` with the account or organisation that hosts the
repository.

### Requirements

* Python 3.10+ (3.12 recommended)
* Java 17+ (OpenJDK). On Debian/Ubuntu: `sudo apt-get install openjdk-17-jre`
* ffmpeg in PATH (or set `FFMPEG_PATH` in `.env`)

## Installation

For platform-specific setup steps (Linux/macOS, Windows, Docker) see [INSTALL.md](INSTALL.md).

Quick start:
- Linux/macOS: `./scripts/run.sh`
- Windows: `./scripts/install.ps1`
- Docker: `docker compose up --build`


## Configuration

If you ran `./scripts/install.sh` the script created a `.env` file and asked for
the most important values. Otherwise copy the example and fill in the required
variables:

* `DISCORD_TOKEN` &ndash; your Discord bot token
* `OPENAI_API_KEY` &ndash; your OpenAI API key

Optional variables include `COMMAND_PREFIX`, `GUILD_ID`, `LAVALINK_HOST`,
`LAVALINK_PORT`, `LAVALINK_PASSWORD`, `OPENAI_MODEL`, `ICS_URL`, `F1_CHANNEL_ID`,
`LOCAL_TIMEZONE`, `ELBOT_DATA_DIR`, `ELBOT_SERVICE` and `PORT`.

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

Activate the virtual environment and run the entry point:

```bash
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m elbot.main
```

You can also use the helper script:

```bash
./scripts/run.sh
# Or start Lavalink automatically:
./scripts/run.sh --with-lavalink
```

## Running Lavalink

Elbot includes an auto-launcher that downloads and starts Lavalink when the
bot runs, selecting a free local port and writing logs to a per-user data
directory (for example `~/.local/share/Elbot/ElbotTeam` on Linux). Set
`ELBOT_DATA_DIR` if you prefer to store the Lavalink files elsewhere. If Java
17+ is not present on the system, Elbot will automatically download a portable
Temurin (Adoptium) JRE 17 to the same directory and use it, so music works out
of the box.

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
[`dev.lavalink.youtube`](https://github.com/lavalink-devs/youtube-source)
plugin. Add the plugin to your `application.yml` (or docker environment) and
remove the legacy `youtube` source block to avoid loading the deprecated
implementation:

```yaml
plugins:
  - dependency: "dev.lavalink.youtube:1.7.2"  # check GitHub for the latest tag

source-managers:
  youtube:
    enabled: false
```

After editing the file restart Lavalink. To update the plugin later simply
replace the entry above with the new version string and restart the server; no
code changes in Elbot are required.

Elbot will fall back to direct stream extraction with `yt-dlp` when Lavalink is
unavailable or throttled. Set the `YTDLP_COOKIES_FILE` environment variable to
point at a Netscape cookie file if you need to access age-restricted videos.

## Docker

Build the container and run the bot and portal with Docker Compose:

```bash
docker compose up --build
```

This starts the bot, management portal and a Lavalink instance for music playback.

The Dockerfile installs `ffmpeg` automatically so music playback works out of the box.

The portal exposes port `8000` by default and can be changed with the `PORT`
environment variable.

## Windows notes

- Use `scripts\install.ps1` for a guided setup that installs dependencies and the Windows service.
- `elbot-install-service` can also be run manually; it installs and starts the service using pywin32.

## macOS notes

`elbot-install-service` installs a LaunchAgent in `~/Library/LaunchAgents/com.elbot.bot.plist` that runs at login
and restarts the bot if it exits.

## Updating

To pull the latest version of Elbot from GitHub:

```bash
./scripts/run.sh update
```

To upgrade the YouTube stack:

- Update `yt-dlp` with `pip install -U yt-dlp` (or bump the version in
  `pyproject.toml`) and restart the bot. The helper module lives in
  `elbot/audio/ytdlp_helper.py` if you need to adjust extraction options.
- Update the Lavalink YouTube plugin by editing the version in your
  `application.yml` as shown above, then restart the Lavalink process. No Python
  changes are necessary.

## Web Portal

Elbot includes a lightweight management portal built with Flask. After the
dependencies are installed you can launch it with:

```bash
elbot-portal
```

Open your browser to <http://localhost:8000> by default. The listening port can
be changed with the `PORT` environment variable. The portal lets you view logs,
switch Git branches and run `run.sh update`. It also provides a button
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
runs `scripts/run.sh update` once per day and restarts the bot service after each
update. This allows unattended updates and restarts.

To enable the feature temporarily:

```bash
AUTO_UPDATE=1 elbot-portal
```

Or export the variable in your shell for persistent use.

## Architecture overview

The project is organised into a few key directories:

- **`elbot/`** – core package with the main entry point, configuration helper,
  service installation logic and the Flask management portal.
- **`cogs/`** – individual modules implementing slash commands for chat,
  image generation, music playback and Formula&nbsp;1 updates.
- **`scripts/`** – helper shell scripts for installing dependencies,
  running the bot and updating from Git.
- **`tests/`** – pytest-based test suite.

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
