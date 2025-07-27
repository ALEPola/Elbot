#!/usr/bin/env bash
# Manage Elbot, Lavalink and project updates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

run_lavalink() {
    local LAVA_DIR="$ROOT_DIR/lavalink"
    local JAR="$LAVA_DIR/Lavalink.jar"
    local VERSION="4.0.4"
    local JAR_URL="https://github.com/freyacodes/Lavalink/releases/download/${VERSION}/Lavalink.jar"
    local ENV_FILE="$ROOT_DIR/.env"

    mkdir -p "$LAVA_DIR"
    cd "$LAVA_DIR"

    if [ ! -f "$JAR" ]; then
        echo "Downloading Lavalink ${VERSION}..."
        if command -v wget >/dev/null 2>&1; then
            wget "$JAR_URL" -O "$JAR"
        elif command -v curl >/dev/null 2>&1; then
            curl -L "$JAR_URL" -o "$JAR"
        else
            echo "Error: wget or curl is required to download Lavalink." >&2
            exit 1
        fi
    fi

    if [ -f "$ENV_FILE" ]; then
        # shellcheck disable=SC1090
        source "$ENV_FILE"
    fi

    PORT="${LAVALINK_PORT:-2333}"
    PASSWORD="${LAVALINK_PASSWORD:-youshallnotpass}"

    cat > application.yml <<EOF_CONF
server:
  port: ${PORT}
  address: 0.0.0.0
lavalink:
  server:
    password: ${PASSWORD}
EOF_CONF

    case "${1:-}" in
        start)
            echo "Starting Lavalink..."
            exec java -jar "$JAR"
            ;;
        stop)
            if pkill -f "$JAR"; then
                echo "Lavalink stopped."
            else
                echo "Lavalink is not running."
            fi
            ;;
        *)
            echo "Lavalink prepared in $LAVA_DIR"
            echo "Run '$0 lavalink start' to launch it or '$0 lavalink stop' to stop a running instance."
            ;;
    esac
}

run_update() {
    local ENV_FILE="$ROOT_DIR/.env"
    local BACKUP_FILE="$ENV_FILE.bak"

    restore_env() {
        if [ -f "$BACKUP_FILE" ]; then
            mv "$BACKUP_FILE" "$ENV_FILE"
        fi
    }

    trap restore_env EXIT

    cd "$ROOT_DIR"

    if [ -f "$ENV_FILE" ]; then
        cp "$ENV_FILE" "$BACKUP_FILE"
    fi

    echo "[1/2] Pulling latest changes..."
    git pull --ff-only

    echo "[2/2] Updating Python dependencies..."
    if [ -d ".venv" ]; then
        source .venv/bin/activate
        pip install --upgrade -e .
    fi
}

case "${1:-}" in
    lavalink)
        shift
        run_lavalink "$@"
        exit 0
        ;;
    update)
        shift
        run_update "$@"
        exit 0
        ;;
esac

START_LAVALINK=0

if [ "${1:-}" = "--with-lavalink" ]; then
    START_LAVALINK=1
    shift
fi

# Ensure the project is installed
if [ ! -d "$ROOT_DIR/.venv" ]; then
    "$SCRIPT_DIR/install.sh" --yes
fi

# Create a default .env if missing
if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo "Created $ROOT_DIR/.env from template. Please edit it with your tokens."
fi

source "$ROOT_DIR/.venv/bin/activate"

if [ "$START_LAVALINK" -eq 1 ]; then
    run_lavalink start &
    LAVALINK_PID=$!
fi

python -m elbot.main "$@"

if [ "$START_LAVALINK" -eq 1 ]; then
    run_lavalink stop >/dev/null 2>&1 || true
    if [ -n "${LAVALINK_PID:-}" ]; then
        kill "$LAVALINK_PID" >/dev/null 2>&1 || true
    fi
fi

