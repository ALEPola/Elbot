# Elbot

Elbot is a modular Discord bot powered by [Nextcord](https://github.com/nextcord/nextcord) and OpenAI. It includes chat, image generation and music playback features.

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
```

Linux users can run `./scripts/install.sh` to be guided through installing system packages and Python dependencies. Pass `--yes` to skip the prompts and install automatically.

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your tokens:

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

## Windows notes

On Windows, you may need to install [FFmpeg](https://ffmpeg.org/) separately and ensure `ffmpeg.exe` is in your `PATH`.

## Updating

To pull the latest version of Elbot from GitHub:

```bash
./scripts/update.sh
```


