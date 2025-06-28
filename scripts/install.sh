#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

## Guided setup script for the Elbot project.
## Creates a Python virtual environment, installs dependencies and optionally
## registers a service.

usage() {
    cat <<EOF
Usage: $(basename "$0") [--yes]

--yes    Run non-interactively and assume "yes" for all prompts.
EOF
}

ASK=1

case "${1:-}" in
    -y|--yes)
        ASK=0
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    "") ;;
    *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
esac

# If not running in a TTY (e.g. via another script) fall back to --yes
if [ ! -t 0 ]; then
    ASK=0
fi

prompt_yes_no() {
    local prompt="$1" default=${2:-Y} reply
    if [ "$ASK" -eq 0 ]; then
        return 0
    fi
    while true; do
        read -rp "$prompt [$default] " reply
        reply=${reply:-$default}
        case "$reply" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) echo "Please answer y or n." ;;
        esac
    done
}

echo "\n*** Elbot Setup ***"
echo "This script will install dependencies and create a virtual environment in"
echo "$ROOT_DIR".
prompt_yes_no "Continue?" Y || exit 0

echo "\n[1/6] Checking system packages..."

# Install system packages if apt-get is available (Ubuntu/Debian)
if command -v apt-get >/dev/null 2>&1; then
    if prompt_yes_no "Install required system packages using apt-get?" Y; then
        # Use sudo only if available and avoid interactive prompts
        if sudo -n true 2>/dev/null; then
            SUDO="sudo -n"
        else
            SUDO="sudo"
        fi
        export DEBIAN_FRONTEND=noninteractive
        $SUDO apt-get -y update
        $SUDO apt-get -y install python3 python3-venv python3-pip ffmpeg
    fi
else
    echo "apt-get not found; please ensure Python 3, pip and ffmpeg are installed"
fi

echo "\n[2/6] Creating virtual environment..."

if [ -d "$ROOT_DIR/.venv" ]; then
    echo "Virtual environment already exists."
else
    if prompt_yes_no "Create virtual environment at .venv?" Y; then
        python3 -m venv "$ROOT_DIR/.venv"
    fi
fi

source "$ROOT_DIR/.venv/bin/activate"
PYTHON="$VIRTUAL_ENV/bin/python"

# Copy example environment if none exists
if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

update_env_var() {
    local var="$1" value="$2"
    if grep -q "^$var=" "$ROOT_DIR/.env"; then
        sed -i.bak "s|^$var=.*|$var=$value|" "$ROOT_DIR/.env" && rm -f "$ROOT_DIR/.env.bak"
    else
        echo "$var=$value" >>"$ROOT_DIR/.env"
    fi
}

if [ "$ASK" -eq 1 ]; then
    echo "\n[3/6] Configuring environment variables..."
    read -rp "Discord bot token: " discord_token
    read -rp "OpenAI API key: " openai_key
    read -rp "Guild ID (optional): " guild_id
    update_env_var "DISCORD_BOT_TOKEN" "$discord_token"
    update_env_var "OPENAI_API_KEY" "$openai_key"
    if [ -n "$guild_id" ]; then
        update_env_var "GUILD_ID" "$guild_id"
    fi
    echo "Edit $ROOT_DIR/.env to configure additional settings."
fi

echo "\n[4/6] Installing Python dependencies..."

if prompt_yes_no "Install Python packages with pip?" Y; then
    pip install --upgrade pip
    if [ -f "$ROOT_DIR/pyproject.toml" ]; then
        # Install in editable mode if a pyproject exists
        pip install -e "$ROOT_DIR"
    else
        # Fall back to requirements.txt for older setups
        pip install -r "$ROOT_DIR/requirements.txt"
    fi
    $PYTHON -m textblob.download_corpora
fi

echo "\n[5/6] Installing service..."

if prompt_yes_no "Install, enable and start Elbot as a service?" Y; then
    "$PYTHON" -m elbot.service_install
fi

echo "\n[6/6] Installation complete."
echo "Activate the virtual environment with: source .venv/bin/activate"
