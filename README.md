# Elbot

Elbot is a modular Discord bot powered by [Nextcord](https://github.com/nextcord/nextcord) and OpenAI. It includes chat, image generation and music playback features.

## Features

- **Chat** – interact with OpenAI via the `/chat` command. Recent messages are kept briefly to provide context.
- **DALL·E** – generate images using the `/dalle` command.
- **F1** – receive Formula&nbsp;1 schedules, countdowns and race results. Set `ICS_URL` for the calendar feed and `LOCAL_TIMEZONE` for your local zone.
- **Music** – play audio from YouTube links. Requires `ffmpeg` installed.
- **Diagnostic** – utility commands for bot admins.

See `.env.example` for all configuration variables, including `ELBOT_SERVICE` and `PORT` which are used by the management portal.

## Requirements

- Python 3.9+
- `ffmpeg` installed and on your `PATH` (required for music commands)
- A Discord bot token
- An OpenAI API key

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

Linux users can run `./scripts/install.sh` to be guided through installing system packages and Python dependencies. Pass `--yes` to skip the prompts and install automatically.

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in the required variables:

* `DISCORD_BOT_TOKEN` &ndash; your Discord bot token
* `OPENAI_API_KEY` &ndash; your OpenAI API key

Optional variables include `COMMAND_PREFIX`, `GUILD_ID`, `YOUTUBE_COOKIES_PATH`,
`OPENAI_MODEL`, `ICS_URL` and `F1_CHANNEL_ID`.
`ICS_URL` may use a `webcal://` address. It will be converted to `https://` automatically.
See `.env.example` for an example Formula&nbsp;1 feed.

```bash
cp .env.example .env
# edit .env with your values
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

Elbot ships with a simple management portal built with Flask. To start it:

```bash
elbot-portal
```

The portal lets you view the bot logs, switch Git branches, trigger
`update.sh` and restart the system service running the bot. It listens on the
port specified by the `PORT` environment variable (default `8000`).

### ELBOT_SERVICE

The portal restarts the bot via `systemctl`. It targets the service
specified by the `ELBOT_SERVICE` environment variable, which defaults to
`elbot.service`. Set this variable before launching the portal if your
systemd unit has a different name.

## Sound assets

The optional `/moan` slash command in the music cog expects the file
`sounds/56753004_girl-moaning_by_a-sfx_preview.mp3` to exist relative to the
project root. This MP3 is not bundled with the repository. To enable the command,
create a `sounds/` directory at the root of the project and place the downloaded
file there (for example from [Freesound](https://freesound.org/people/a-sfx/sounds/56753004/)).
If the file is missing, the command will respond that the sound effect could not
be found.



## Testing

Install Elbot in editable mode so the local package is available and the extras required by the tests (such as `nextcord[voice]` and `aiohttp[speedups]`) are installed. These packages are bundled in the `test` extras group:

```bash
pip install -e .[test]
```

Then run the test suite:

```bash
pytest
```
