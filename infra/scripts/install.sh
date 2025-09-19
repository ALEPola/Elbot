#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi
PYTHONPATH_VALUE="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

check_ports() {
  "$PYTHON_BIN" - <<'PY'
from elbot.core import network

PORT_HINTS = {2333: "LAVALINK_PORT", 8000: "PORT"}
conflicts = network.detect_port_conflicts(PORT_HINTS.keys())
if conflicts:
    ports = ", ".join(str(port) for port in conflicts)
    print(f"Warning: the following ports appear to be in use: {ports}.")
    hints = [PORT_HINTS[port] for port in conflicts if port in PORT_HINTS]
    if hints:
        if len(hints) == 1:
            print(f"Stop other services or adjust {hints[0]} in .env before continuing.")
        else:
            joined = " and ".join(hints)
            print(f"Stop other services or adjust {joined} in .env before continuing.")
    else:
        print("Stop other services or update your configuration to avoid the conflict.")
PY
}

PYTHONPATH="$PYTHONPATH_VALUE" check_ports
PYTHONPATH="$PYTHONPATH_VALUE" "$PYTHON_BIN" -m elbot.cli install "$@"

WRAPPER="$ROOT_DIR/.venv/bin/elbotctl"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  cat <<'EOF' > "$WRAPPER"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/python" -m elbot.cli "$@"
EOF
  chmod +x "$WRAPPER"
fi
