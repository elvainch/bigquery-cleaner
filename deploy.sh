#!/usr/bin/env bash
set -euo pipefail

# Thin wrapper that delegates to the Python deploy script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer python3, then python; on Windows Git Bash, fall back to 'py -3'.
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/scripts/deploy.py" "$@"
elif command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT_DIR/scripts/deploy.py" "$@"
elif command -v py >/dev/null 2>&1; then
  exec py -3 "$SCRIPT_DIR/scripts/deploy.py" "$@"
else
  echo "Python 3 not found. Install Python 3 and ensure 'python3' or 'python' or 'py' is on PATH." >&2
  exit 1
fi
