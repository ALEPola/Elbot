#!/usr/bin/env bash

set -euo pipefail

# Super automated host bootstrapper for Elbot.
#
# This script installs the OS prerequisites, prepares the .env file, ensures
# the default ports are available, and finally runs scripts/install.sh in
# unattended mode. It is intended for Debian/Ubuntu hosts.

log() {
    printf '[provision] %s\n' "$*"
}

err() {
    printf '[provision] ERROR: %s\n' "$*" >&2
}

need_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "Required command '$1' not found in PATH"
        exit 1
    fi
}

# Determine repository root. When running from within the repo the script lives
# in scripts/provision.sh, so we can compute ROOT_DIR relative to it. If the
# script is executed from another location we fall back to $PWD.
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
else
    ROOT_DIR="$(pwd)"
fi

ENV_FILE="$ROOT_DIR/.env"
EXAMPLE_ENV="$ROOT_DIR/.env.example"
INSTALLER="$ROOT_DIR/scripts/install.sh"

if [[ ! -x "$INSTALLER" ]]; then
    err "scripts/install.sh not found. Run this script from inside the cloned repository."
    exit 1
fi

# Decide how to elevate privileges when necessary.
if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        err "Root privileges are required. Install sudo or run this script as root."
        exit 1
    fi
fi

APT_PACKAGES=(
    git
    python3
    python3-venv
    python3-pip
    ffmpeg
    openjdk-17-jre-headless
    curl
    lsof
    psmisc
)

if command -v apt-get >/dev/null 2>&1; then
    log "Installing OS prerequisites via apt-get"
    export DEBIAN_FRONTEND=noninteractive
    ${SUDO:-} apt-get update -y
    ${SUDO:-} apt-get install -y "${APT_PACKAGES[@]}"
else
    err "apt-get not found. This bootstrap script currently supports Debian/Ubuntu hosts only."
    exit 1
fi

need_command git
need_command python3
need_command ffmpeg
need_command java

# Ensure .env exists so we can populate it without relying on install.sh prompts.
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$EXAMPLE_ENV" ]]; then
        cp "$EXAMPLE_ENV" "$ENV_FILE"
    else
        touch "$ENV_FILE"
    fi
fi

update_env_var() {
    local var="$1" value="$2"
    if [[ -z "$value" ]]; then
        return
    fi
    if grep -q "^${var}=" "$ENV_FILE"; then
        # Use a temp file to remain POSIX compliant across sed implementations.
        tmp_file="$(mktemp)"
        sed "s|^${var}=.*|${var}=${value}|" "$ENV_FILE" >"$tmp_file"
        mv "$tmp_file" "$ENV_FILE"
    else
        printf '%s=%s\n' "$var" "$value" >>"$ENV_FILE"
    fi
}

require_env_var() {
    local var="$1" value="$2"
    if [[ -z "$value" ]]; then
        if ! grep -q "^${var}=" "$ENV_FILE"; then
            err "Environment variable $var is required. Set $3 before running or pre-populate $ENV_FILE."
            exit 1
        fi
    else
        update_env_var "$var" "$value"
    fi
}

# Required secrets must be provided through environment variables for unattended installs.
require_env_var "DISCORD_TOKEN" "${ELBOT_DISCORD_TOKEN:-}" "ELBOT_DISCORD_TOKEN"
require_env_var "OPENAI_API_KEY" "${ELBOT_OPENAI_KEY:-}" "ELBOT_OPENAI_KEY"

# Optional values.
update_env_var "GUILD_ID" "${ELBOT_GUILD_ID:-}"
update_env_var "PORTAL_PORT" "${ELBOT_PORTAL_PORT:-}"
update_env_var "LAVALINK_SERVER_PORT" "${ELBOT_LAVALINK_PORT:-}"

# Manage ports before the installer enables services.
ELBOT_PORTS="${ELBOT_PORTS:-8080 2333}"

close_port() {
    local port="$1"
    local pids
    pids=$(${SUDO:-} lsof -t -i TCP:"$port" -s TCP:LISTEN 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "Port $port is in use by PID(s) $pids. Attempting to terminate."
        local pid
        for pid in $pids; do
            if ${SUDO:-} kill -0 "$pid" 2>/dev/null; then
                ${SUDO:-} kill "$pid" 2>/dev/null || true
            fi
        done
        sleep 2
        local still_alive=()
        for pid in $pids; do
            if ${SUDO:-} kill -0 "$pid" 2>/dev/null; then
                still_alive+=("$pid")
            fi
        done
        if (( ${#still_alive[@]} > 0 )); then
            log "Force killing remaining PID(s): ${still_alive[*]}"
            for pid in "${still_alive[@]}"; do
                ${SUDO:-} kill -9 "$pid" 2>/dev/null || true
            done
        fi
    fi
}

for port in $ELBOT_PORTS; do
    close_port "$port"
    if ${SUDO:-} lsof -t -i TCP:"$port" -s TCP:LISTEN >/dev/null 2>&1; then
        err "Unable to free port $port. Resolve the conflict and rerun the script."
        exit 1
    fi
    log "Port $port is available."
done

log "Launching scripts/install.sh in unattended mode"
ELBOT_SETUP_NONINTERACTIVE=1 "$INSTALLER" --yes

log "Provisioning complete. Review $ENV_FILE for additional configuration."
