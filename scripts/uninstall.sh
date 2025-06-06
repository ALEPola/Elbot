#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet elbot.service; then
        sudo systemctl stop elbot.service
    fi
    if systemctl is-enabled --quiet elbot.service; then
        sudo systemctl disable elbot.service
    fi
    if [ -f /etc/systemd/system/elbot.service ]; then
        sudo rm /etc/systemd/system/elbot.service
        sudo systemctl daemon-reload
    fi
fi

if [ "$1" = "--delete" ]; then
    echo "Removing $ROOT_DIR"
    rm -rf "$ROOT_DIR"
fi

echo "Elbot uninstalled."
