#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root even if called via symlink
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DELETE_SRC=0
PURGE_PKGS=0

# Parse args
for arg in "${@:-}"; do
  case "$arg" in
    --delete) DELETE_SRC=1 ;;
    --purge)  PURGE_PKGS=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Uninstalling Elbot …"

# 1) Stop & remove systemd unit (if systemd exists)
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files | grep -q '^elbot\.service'; then
    echo " - Stopping elbot.service"
    systemctl is-active --quiet elbot.service && sudo systemctl stop elbot.service || true
    echo " - Disabling elbot.service"
    systemctl is-enabled --quiet elbot.service && sudo systemctl disable elbot.service || true
    echo " - Removing unit file"
    sudo rm -f /etc/systemd/system/elbot.service
    sudo systemctl daemon-reload
  fi
fi

# 2) Kill stray Lavalink (best-effort)
echo " - Killing Lavalink (if running)"
pkill -f 'java.*Lavalink\.jar' 2>/dev/null || true

# 3) Remove per-user Lavalink data
echo " - Removing Lavalink data dirs"
rm -rf ~/.elbot_lavalink ~/.local/share/Elbot

# 4) Optionally delete the source directory
if [[ $DELETE_SRC -eq 1 ]]; then
  echo " - Deleting source at: $ROOT_DIR"

  # Deactivate venv if we’re inside it
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo "   (deactivating virtualenv)"
    deactivate 2>/dev/null || true
  fi

  # cd out of the directory we’re about to delete
  if [[ "$PWD" == "$ROOT_DIR"* ]]; then
    cd ~
  fi

  # Fix ownership (handles root-owned .pyc from service runs)
  echo "   (fixing ownership before deletion)"
  sudo chown -R "$USER":"$USER" "$ROOT_DIR" 2>/dev/null || true

  # Remove venv and tree
  rm -rf "$ROOT_DIR"
fi

# 5) Optional: purge packages installed for the bot
if [[ $PURGE_PKGS -eq 1 ]] && command -v apt-get >/dev/null 2>&1; then
  echo " - Purging optional packages (Java 17 + ffmpeg)"
  sudo apt-get remove --purge -y openjdk-17-jre ffmpeg || true
  sudo apt-get autoremove -y || true
fi

echo "✅ Elbot uninstalled."
