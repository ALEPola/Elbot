ï»¿#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m elbot.cli install "$@"

WRAPPER="$ROOT_DIR/.venv/bin/elbotctl"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  cat <<'EOF' > "$WRAPPER"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/python" -m elbot.cli "$@"
EOF
  chmod +x "$WRAPPER"
fi
