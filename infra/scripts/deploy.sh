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

    [[ "$remote_user" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || { log 'Invalid deployment user'; exit 1; }
    [[ "$DEPLOY_HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || { log 'Use a hostname or IPv4 deployment address'; exit 1; }
    [[ "$remote_path" =~ ^(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+/[Ee]lbot/?$ ]] || { log 'Use an absolute application directory ending in elbot'; exit 1; }
    remote_compose="${DEPLOY_COMPOSE_FILE:-infra/docker/docker-compose.yml}"
    [[ "$remote_compose" =~ ^[A-Za-z0-9_./-]+$ && "$remote_compose" != /* && "$remote_compose" != *'..'* ]] || { log 'Invalid relative Compose path'; exit 1; }

    ssh_opts=("-o" "BatchMode=yes" "-o" "StrictHostKeyChecking=yes")
    tmp_key=""
    tmp_hosts=""
    cleanup() {
        [[ -z "$tmp_key" ]] || rm -f -- "$tmp_key"
        [[ -z "$tmp_hosts" ]] || rm -f -- "$tmp_hosts"
    }
    trap cleanup EXIT
    # Host keys must be verified out-of-band, never accepted from an
    # unauthenticated ssh-keyscan during the deployment itself.
    if [[ -n "${DEPLOY_KNOWN_HOSTS:-}" ]]; then
        tmp_hosts="$(mktemp)"
        chmod 600 "$tmp_hosts"
        printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$tmp_hosts"
        ssh_opts+=("-o" "UserKnownHostsFile=$tmp_hosts")
    elif [[ ! -s "${HOME}/.ssh/known_hosts" ]]; then
        log 'Verified SSH known_hosts or DEPLOY_KNOWN_HOSTS is required'
        exit 1
    fi
    if [[ "${DEPLOY_INCLUDE_ENV:-0}" == "1" ]]; then
        log 'Deployment cannot upload environment files; configure secrets on the host'
        exit 1
    fi
    if [[ -n "${SSH_PRIVATE_KEY:-}" ]]; then
        tmp_key="$(mktemp)"
        printf '%s\n' "$SSH_PRIVATE_KEY" >"$tmp_key"
        chmod 600 "$tmp_key"
        ssh_opts+=("-i" "$tmp_key")
    fi

    log "Ensuring remote directory $remote:$remote_path exists"
    ssh "${ssh_opts[@]}" "$remote" "mkdir -p '$remote_path'"

    # Excluded destination files remain protected from rsync --delete.
    excludes=(".git" ".venv" "__pycache__" "*.pyc" "chat_history" "logs" "*.log" "node_modules" ".env" ".env.*" "data" "*cookies*.txt" "*.pem")
    rsync_args=()
    for pattern in "${excludes[@]}"; do
        rsync_args+=("--exclude" "$pattern")
    done

    log "Syncing repository to $remote:$remote_path"
    rsync -az --delete "${rsync_args[@]}" -e "ssh ${ssh_opts[*]}" ./ "$remote:$remote_path/"

    remote_cmd="cd '$remote_path' && "
    remote_cmd+="if docker compose version >/dev/null 2>&1; then "
    remote_cmd+="docker compose -f '$remote_compose' pull && "
    remote_cmd+="docker compose -f '$remote_compose' up -d --build --remove-orphans; "
    remote_cmd+="else docker-compose -f '$remote_compose' pull && "
    remote_cmd+="docker-compose -f '$remote_compose' up -d --build --remove-orphans; fi"

    log "Recreating containers on remote host"
    ssh "${ssh_opts[@]}" "$remote" "$remote_cmd"

    log "Remote deployment finished"
    exit 0
fi

# Local systemd deployment ----------------------------------------------------
if [[ "${SYSTEMD_DEPLOY:-0}" == "1" ]]; then
    need_cmd git
    need_cmd systemctl

    echo "=== Pulling latest changes ==="
    git pull --ff-only

    echo "=== Installing Python dependencies ==="
    python="$(pwd)/.venv/bin/python"
    [[ -x "$python" ]] || { log 'Create the application virtual environment first'; exit 1; }
    "$python" -m pip install -c requirements-pi.lock -e .

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
