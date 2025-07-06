# Installation and Management Guide

This document explains how to install, run, update and uninstall **Elbot** on Linux/macOS and Windows.

## Prerequisites

- Python 3.9 or newer installed and available on your `PATH`
- `git` command-line tools
- On Linux/macOS: `bash` shell and a C compiler
- For music playback: `ffmpeg` and a running Lavalink server (Java 17+)

## 1. Obtain the sources

```bash
git clone <repository-url>
cd Elbot
```

## 2. Create a virtual environment

### Linux/macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows
```cmd
python -m venv .venv
.\.venv\Scripts\activate
```

## 3. Install Python dependencies

```bash
pip install -U pip
pip install -e .
```

`pip install -r requirements.txt` is an alternative if editable mode is not required.

## 4. Configure environment variables

Copy the example file and edit it with your tokens:

```bash
cp .env.example .env
```

Set at least `DISCORD_BOT_TOKEN` and `OPENAI_API_KEY`. Other values such as Lavalink connection details can remain at their defaults.

## 5. Register as a service (optional)

Elbot can run automatically using a system service. Run:

```bash
elbot-install-service
```

- On Linux this writes `/etc/systemd/system/elbot.service` and enables it.
- On Windows it creates a Windows service called *Elbot* that starts automatically.

To remove the service later run:

```bash
elbot-install-service --remove
```

## 6. Running Elbot

Activate the environment and start the main module:

```bash
source .venv/bin/activate       # Windows: .\.venv\Scripts\activate
python -m elbot.main
```

Alternatively use the helper script:

```bash
./scripts/run.sh
```

To access the management portal run:

```bash
elbot-portal
```

The portal listens on `http://localhost:8000` by default. Set the `PORT` variable in `.env` if you need a different port.

## 7. Updating

Fetch the latest code and upgrade dependencies with:

```bash
./scripts/update.sh
```

This runs `git pull` and reinstalls the package inside `.venv`. If you have installed Elbot as a service, restart it afterwards:

```bash
sudo systemctl restart elbot.service      # Linux
sc stop Elbot & sc start Elbot            # Windows
```

## 8. Uninstallation

Stop and remove the service first:

```bash
elbot-install-service --remove
```

On Linux you can also run:

```bash
./scripts/uninstall.sh
```

to disable and remove the systemd unit. Finally delete the project directory and the `.venv` folder if you no longer need them.

