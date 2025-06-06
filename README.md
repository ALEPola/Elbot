# Elbot

Elbot is a Discord bot powered by [nextcord](https://github.com/nextcord/nextcord) and OpenAI. It provides a set of cogs for chatting, generating images with DALL·E, playing music and more.

## Requirements

- Python 3.10 or newer
- A Discord bot token
- An OpenAI API key

## Installation

### Windows

1. Install [Python](https://www.python.org/downloads/) and make sure it is on your `PATH`.
2. Clone the repository and create a virtual environment:

   ```powershell
   git clone https://github.com/yourname/elbot.git
   cd elbot
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e .
   ```

### Linux/macOS

```bash
git clone https://github.com/yourname/elbot.git
cd elbot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
# then edit .env and set DISCORD_BOT_TOKEN and OPENAI_API_KEY
```

## Running the bot

Activate your virtual environment and run:

```bash
python -m elbot.main
```

The provided scripts in the `scripts/` directory can be used to simplify setup and running on Unix systems.
