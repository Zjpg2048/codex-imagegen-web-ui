#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [[ "${1-}" != "--help" && "${1-}" != "-h" ]]; then
  if ! "$PYTHON_BIN" -c 'import openai' >/dev/null 2>&1; then
    cat >&2 <<'EOF'
Error: Python package "openai" is not installed.

Install dependencies first:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
EOF
    exit 1
  fi
fi

if [[ "${1-}" != "--help" && "${1-}" != "-h" && -z "${OPENAI_API_KEY-}" ]]; then
  cat >&2 <<'EOF'
Error: OPENAI_API_KEY is not set.

Example:
  export OPENAI_API_KEY=your_key_here
  scripts/start.sh --prompt "一个赛博朋克风格的女孩" --count 3
EOF
  exit 1
fi

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" generate.py "$@"
