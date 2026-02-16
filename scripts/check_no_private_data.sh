#!/usr/bin/env bash
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Run this script from inside a git repository." >&2
  exit 2
fi

# High-confidence secret patterns only (to reduce false positives).
# Intentionally avoid generic words like TOKEN/PASSWORD because those appear in docs and code.
PATTERN='(-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-(proj-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z\-_]{35})'

# Scan tracked files only so local virtualenvs and generated assets do not create noise.
if git ls-files -z | xargs -0 rg -n -I -H -e "$PATTERN" --; then
  echo
  echo "Potential private data detected in tracked files. Please rotate and remove before committing." >&2
  exit 1
fi

echo "No high-confidence private key/token patterns found in tracked files."
