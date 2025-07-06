#!/usr/bin/env bash

# Prepare and optionally launch a Lavalink server.
set -e

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
    wget "$JAR_URL" -O "$JAR"
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
        echo "Stopping Lavalink..."
        PIDS=$(pgrep -f "Lavalink.jar" || true)
        if [ -n "$PIDS" ]; then
            kill $PIDS
            echo "Lavalink stopped."
        else
            echo "Lavalink is not running."
        fi
        ;;
    *)
        echo "Lavalink prepared in $LAVA_DIR"
        echo "Run '$0 start' to launch it or '$0 stop' to terminate.";
        ;;
esac
