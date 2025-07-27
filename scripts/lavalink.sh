#!/usr/bin/env bash

# Prepare and optionally launch a Lavalink server.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAVA_DIR="$ROOT_DIR/lavalink"
JAR="$LAVA_DIR/Lavalink.jar"
VERSION="4.0.4"
JAR_URL="https://github.com/freyacodes/Lavalink/releases/download/${VERSION}/Lavalink.jar"
ENV_FILE="$ROOT_DIR/.env"

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
        echo "Run '$0 start' to launch it or '$0 stop' to stop a running instance."
        ;;
esac
