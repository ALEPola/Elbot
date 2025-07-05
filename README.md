# Elbot

Elbot is a modular Discord bot powered by [Nextcord](https://github.com/nextcord/nextcord) and OpenAI. It includes chat, image generation and music playback features. The project ships with a lightweight management portal and supports running as a system service on Linux and Windows.

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Quick start](#quick-start)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the bot](#running-the-bot)
7. [Docker](#docker)
8. [Windows notes](#windows-notes)
9. [Updating](#updating)
10. [Web Portal](#web-portal)
11. [Architecture overview](#architecture-overview)
12. [Command summary](#command-summary)
13. [Sound assets](#sound-assets)
14. [Testing](#testing)

## Features

- **Chat** – interact with OpenAI via the `/chat` command. Recent messages are kept briefly to provide context.
- **Chat summaries** – `/chat_summary` provides an OpenAI generated recap of your conversation and chat history now persists across restarts.
- **DALL·E** – generate images using the `/dalle` command.
- **F1** – receive Formula&nbsp;1 schedules, countdowns and race results. Set `ICS_URL` for the calendar feed and `LOCAL_TIMEZONE` for your local zone.
- **Music** – play audio from YouTube links. Requires `ffmpeg` installed. Track
  info is cached so repeated `/play` queries skip the YouTube search. Providing
  a direct link is fastest. Recent searches are stored on disk so stream URLs
  can be refreshed quickly. Download concurrency can be adjusted with
  `MUSIC_DL_CONCURRENCY`.
- **Playlists** – save and load queues with `/playlist_save` and `/playlist_load`.
- **Diagnostic** – utility commands for bot admins.
- **Moderation** – `/kick`, `/ban`, `/clear_messages` and `/clear_bot_messages` commands for server admins.
- **Portal auto-update** – the Flask portal can report update status and optionally run daily updates.

See `.env.example` for all configuration variables, including `ELBOT_SERVICE` and `PORT` which are used by the management portal.

## Requirements

- Python 3.9+
- `ffmpeg` installed and on your `PATH` (required for music commands; also listed in `requirements.txt`)
- A Discord bot token
- An OpenAI API key

## Quick start

1. Clone the repository and change into the project directory:

```bash
git clone <repository-url>
cd Elbot
```

2. Run the guided setup script (Linux/macOS):

```bash
./scripts/install.sh
```

This installs dependencies, creates a virtual environment and can register a
system service so the bot starts on boot. The script now prompts for your
Discord bot token, OpenAI API key and optional guild ID. When finished, launch
the bot with:

```bash
source .venv/bin/activate
python -m elbot.main
```

To run Elbot continually in the background use:

```bash
elbot-install-service
```

Windows users can follow the manual steps below and use `elbot-install-service`
to create a service after installing the dependencies.

## Installation

### 1. Create a virtual environment

```bash
python3 -m venv .venv
# Windows
#   .venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -U pip
pip install -e .
# Alternatively you can install from requirements.txt
pip install -r requirements.txt
```

After the dependencies are installed, run:

```bash
elbot-install-service
```

This command installs, enables and starts a system service so Elbot runs automatically.
To remove the service later run:

```bash
elbot-install-service --remove
```

Linux users can run `./scripts/install.sh` to be guided through installing system packages (including `ffmpeg`) and Python dependencies. Pass `--yes` to skip the prompts and install automatically.

## Configuration

If you ran `./scripts/install.sh` the script created a `.env` file and asked for
the most important values. Otherwise copy the example and fill in the required
variables:

* `DISCORD_BOT_TOKEN` &ndash; your Discord bot token
* `OPENAI_API_KEY` &ndash; your OpenAI API key

Optional variables include `COMMAND_PREFIX`, `GUILD_ID`, `YOUTUBE_COOKIES_PATH`,
`OPENAI_MODEL`, `ICS_URL`, `F1_CHANNEL_ID`, `LOCAL_TIMEZONE` and `MUSIC_DL_CONCURRENCY`.
`ICS_URL` may use a `webcal://` address. It will be converted to `https://` automatically.
`LOCAL_TIMEZONE` should be an [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) such as `America/New_York`.
See `.env.example` for an example Formula&nbsp;1 feed and common timezone values.

`YOUTUBE_COOKIES_PATH` should point to a cookies file exported from your browser (Netscape format) if you need authenticated YouTube access. Replace the provided `youtube_cookies.txt` with your own file if required.

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
```

## Docker

Build the container and run the bot and portal with Docker Compose:

```bash
docker compose up --build
```

The Dockerfile installs `ffmpeg` automatically so music playback works out of the box.

The portal exposes port `8000` by default and can be changed with the `PORT`
environment variable.

## Windows notes

On Windows, you may need to install [FFmpeg](https://ffmpeg.org/) separately and ensure `ffmpeg.exe` is in your `PATH`.
Running `elbot-install-service` will create and start a Windows service so the bot runs in the background.

## Updating

To pull the latest version of Elbot from GitHub:

```bash
./scripts/update.sh
```

## Web Portal

Elbot includes a lightweight management portal built with Flask. After the
dependencies are installed you can launch it with:

```bash
elbot-portal
```

Open your browser to <http://localhost:8000> by default. The listening port can
be changed with the `PORT` environment variable. The portal lets you view logs,
switch Git branches and run the `update.sh` script. It also provides a button
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
runs `scripts/update.sh` once per day and restarts the bot service after each
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
- Music queue commands: `/play`, `/skip`, `/pause`, `/resume`, `/queue`,
  `/stop`, plus playlist management (`/playlist_save`, `/playlist_load`, `/playlist_list`, `/playlist_delete`).
- Formula&nbsp;1 commands: `/f1_schedule`, `/f1_countdown`, `/f1_results`,
  `/f1_subscribe` and `/f1_unsubscribe`.
- Diagnostic commands: `/uptime` and `/ping`.
- Moderation commands: `/kick`, `/ban`, `/clear_messages` and `/clear_bot_messages`.

## Sound assets

The optional `/moan` slash command in the music cog expects the file
`sounds/56753004_girl-moaning_by_a-sfx_preview.mp3` to exist relative to the
project root. This MP3 is not bundled with the repository. To enable the command,
create a `sounds/` directory at the root of the project and place the downloaded
file there (for example from [Freesound](https://freesound.org/people/a-sfx/sounds/56753004/)).
If the file is missing, the command will respond that the sound effect could not
be found.



## Testing

Install Elbot in editable mode so the local package is available and the extras required by the tests (such as `nextcord[voice]`, `aiohttp[speedups]` and `pytest-asyncio`) are installed. These packages are bundled in the `test` extras group:

```bash
pip install -e .[test]
```

Then run the test suite:

```bash
pytest
```
