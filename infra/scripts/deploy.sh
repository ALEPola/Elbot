#!/usr/bin/env bash

set -euo pipefail

log() {
    printf '[deploy] %s\n' "$*"
}

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        log "Required command '$1' not found in PATH"
        exit 1
    fi
}

# Remote deployment ---------------------------------------------------------
if [[ -n "${DEPLOY_HOST:-}" ]]; then
    need_cmd rsync
    need_cmd ssh

    remote_user="${DEPLOY_USER:-deploy}"
    remote_path="${DEPLOY_PATH:-/opt/elbot}"
    remote="${remote_user}@${DEPLOY_HOST}"

    ssh_opts=("-o" "StrictHostKeyChecking=no")
    tmp_key=""
    if [[ -n "${SSH_PRIVATE_KEY:-}" ]]; then
        tmp_key="$(mktemp)"
        printf '%s\n' "$SSH_PRIVATE_KEY" >"$tmp_key"
        chmod 600 "$tmp_key"
        ssh_opts+=("-i" "$tmp_key")
    fi

    log "Ensuring remote directory $remote:$remote_path exists"
    ssh "${ssh_opts[@]}" "$remote" "mkdir -p '$remote_path'"

    # Build rsync exclude list. We skip .env unless explicitly requested.
    excludes=(".git" ".venv" "__pycache__" "*.pyc" "chat_history" "logs" "*.log" "node_modules")
    if [[ "${DEPLOY_INCLUDE_ENV:-0}" != "1" ]]; then
        excludes+=(".env")
    fi
    rsync_args=()
    for pattern in "${excludes[@]}"; do
        rsync_args+=("--exclude" "$pattern")
    done

    log "Syncing repository to $remote:$remote_path"
    rsync -az --delete "${rsync_args[@]}" -e "ssh ${ssh_opts[*]}" ./ "$remote:$remote_path/"

    remote_compose="${DEPLOY_COMPOSE_FILE:-infra/docker/docker-compose.yml}"
    remote_cmd="cd '$remote_path' && "
    remote_cmd+="if docker compose version >/dev/null 2>&1; then "
    remote_cmd+="docker compose -f '$remote_compose' pull && "
    remote_cmd+="docker compose -f '$remote_compose' up -d --build --remove-orphans; "
    remote_cmd+="else docker-compose -f '$remote_compose' pull && "
    remote_cmd+="docker-compose -f '$remote_compose' up -d --build --remove-orphans; fi"

    log "Recreating containers on remote host"
    ssh "${ssh_opts[@]}" "$remote" "$remote_cmd"

    if [[ -n "$tmp_key" ]]; then
        rm -f "$tmp_key"
    fi

    log "Remote deployment finished"
    exit 0
fi

# Local systemd deployment ----------------------------------------------------
if [[ "${SYSTEMD_DEPLOY:-0}" == "1" ]]; then
    need_cmd git
    need_cmd python3
    need_cmd systemctl

    echo "=== Pulling latest changes ==="
    git pull --ff-only

    echo "=== Installing Python dependencies ==="
    python3 -m pip install -r requirements.txt
    python3 -m pip install -e .

    echo "=== Restarting elbot.service ==="
    systemctl restart elbot.service

    echo "=== Deployment complete ==="
    exit 0
fi

# Local deployment ----------------------------------------------------------
need_cmd docker

if docker compose version >/dev/null 2>&1; then
    log "Using docker compose plugin for local deployment"
    docker compose -f infra/docker/docker-compose.yml pull
    docker compose -f infra/docker/docker-compose.yml up -d --build --remove-orphans
elif command -v docker-compose >/dev/null 2>&1; then
    log "Using docker-compose standalone for local deployment"
    docker-compose -f infra/docker/docker-compose.yml pull
    docker-compose -f infra/docker/docker-compose.yml up -d --build --remove-orphans
else
    log "Neither 'docker compose' nor 'docker-compose' is available"
    exit 1
fi

log "Local deployment finished"
