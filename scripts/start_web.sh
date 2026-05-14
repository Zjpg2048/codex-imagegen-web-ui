#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if ! command -v codex >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Error: codex CLI is not installed or not on PATH.

Install/login first, then retry:
  codex login
EOF
  exit 1
fi

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" webapp.py "$@"
