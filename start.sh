#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--launch" ] || [ "${1:-}" = "--service" ]; then
  mkdir -p "$HOME/.cache"
  exec >>"$HOME/.cache/foodforthought-launch.log" 2>&1
  echo "$(date '+%Y-%m-%d %H:%M:%S') start.sh ${1} in $(pwd)"
fi

PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3.11 or newer is needed. On ChromeOS Linux run: sudo apt-get install -y python3" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 or newer is needed; found $("$PY" --version 2>&1)." >&2
  exit 1
fi

make_venv() {
  "$PY" -m venv .venv >/dev/null 2>&1 && .venv/bin/python -m pip --version >/dev/null 2>&1
}

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "First run: setting up. This takes a minute or two and needs the internet once."
  rm -rf .venv
  if ! make_venv; then
    rm -rf .venv
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update -qq
      sudo apt-get install -y python3-venv
    fi
    if ! make_venv; then
      echo "Could not create a Python environment (python3 -m venv failed)." >&2
      exit 1
    fi
  fi
fi

if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  if ! { .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -e .; } >.venv/install.log 2>&1; then
    cat .venv/install.log >&2
    echo "Setup failed. Check the internet connection and run this again." >&2
    exit 1
  fi
  touch .venv/.installed
fi

exec .venv/bin/python -m foodforthought "$@"
