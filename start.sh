#!/usr/bin/env bash
#
# AsterMem one-command launcher (macOS / Linux)
#
# Background: The manual setup in README has four steps (create venv, install Python deps,
# build Web UI, run server.py). New users most often forget the frontend build, ending up
# with a bare JSON hint page in the browser.
# Design intent: Collapse "install + build + run" into a single ./start.sh. Every step is
# idempotent — already-done steps are skipped, repeated runs take only seconds.
# Windows uses start.bat / start.ps1 with the same logic.
# Key constraints:
#   - Whether to reinstall deps / rebuild frontend is decided by mtime comparison, no
#     network checks, so offline boot works
#   - Missing node/npm only warns, doesn't block (backend still serves /api/agent/call)
#   - Does not touch data/ or config.yaml: port, credentials, etc. are decided by backend
#     first-boot logic
#
# Copyright (c) 2026 Asterove
# AGPL-3.0 License

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

VENV_DIR="$REPO_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
DEPS_MARKER="$VENV_DIR/.astermem-deps-ok"
UI_ENTRY="$REPO_DIR/web-ui/dist/index.html"

REBUILD_UI=0
SKIP_UI=0
REINSTALL=0

if [ -t 1 ]; then
    C_BOLD=$'\033[1m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_BOLD=""; C_WARN=""; C_ERR=""; C_OFF=""
fi

log()  { printf '%s[AsterMem]%s %s\n' "$C_BOLD" "$C_OFF" "$1"; }
warn() { printf '%s[AsterMem]%s %s\n' "$C_WARN" "$C_OFF" "$1" >&2; }
die()  { printf '%s[AsterMem]%s %s\n' "$C_ERR" "$C_OFF" "$1" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./start.sh [options]

  --rebuild-ui   Force rebuild the Web UI (normally detected automatically from source mtimes)
  --skip-ui      Skip frontend check, start backend directly
  --reinstall    Force reinstall Python dependencies
  -h, --help     Show this help

First run automatically creates venv, installs dependencies and builds the Web UI; subsequent runs only perform checks.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --rebuild-ui) REBUILD_UI=1 ;;
        --skip-ui)    SKIP_UI=1 ;;
        --reinstall)  REINSTALL=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

# Find a usable interpreter: prefer 3.11 (project baseline), minimum 3.10
find_python() {
    local candidate
    for candidate in python3.11 python3.12 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$VENV_PYTHON" ]; then
    bootstrap_python="$(find_python)" || die "Python 3.10+ not found. Please install Python 3.11 first (macOS: brew install python@3.11)"
    log "Creating virtual environment venv/ ($("$bootstrap_python" -V 2>&1))"
    "$bootstrap_python" -m venv "$VENV_DIR"
    REINSTALL=1
fi

if [ "$REINSTALL" = 1 ] || [ ! -f "$DEPS_MARKER" ] || [ "$REPO_DIR/requirements.txt" -nt "$DEPS_MARKER" ]; then
    log "Installing Python dependencies (first run takes ~1-3 minutes)"
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install -r requirements.txt
    touch "$DEPS_MARKER"
else
    log "Python dependencies are up to date"
fi

# .env stores API keys only; copy from template if missing so provider config has a place to land
if [ ! -f "$REPO_DIR/.env" ] && [ -f "$REPO_DIR/.env.example" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    log "Generated .env from .env.example (fill in your API key and restart to enable semantic search)"
fi

# A present dist/ says nothing about whether it matches the current source: editing web-ui/
# and restarting used to silently keep serving the previous bundle. Compare source mtimes
# against the built entry instead, so a rebuild is only skipped when it is genuinely current.
ui_is_stale() {
    [ -f "$UI_ENTRY" ] || return 0
    local newer
    newer="$(find "$REPO_DIR/web-ui" \
        -name node_modules -prune -o \
        -name dist -prune -o \
        -name '*.tsbuildinfo' -prune -o \
        -type f -newer "$UI_ENTRY" -print -quit 2>/dev/null)"
    [ -n "$newer" ]
}

if [ "$SKIP_UI" = 1 ]; then
    log "Skipped frontend check (--skip-ui)"
elif [ "$REBUILD_UI" = 1 ] || ui_is_stale; then
    if command -v npm >/dev/null 2>&1; then
        cd "$REPO_DIR/web-ui"
        if [ ! -d node_modules ]; then
            log "Installing frontend dependencies"
            npm install
        fi
        log "Building Web UI"
        npm run build
        cd "$REPO_DIR"
    else
        warn "npm not found, skipping Web UI build: browser will only show the API hint page; AI channel /api/agent/call is unaffected"
        warn "After installing Node.js 18+, run ./start.sh --rebuild-ui to build the frontend"
    fi
else
    log "Web UI build is up to date (newer than all web-ui/ sources)"
fi

log "Starting AsterMem... (Ctrl+C to stop; default credentials: admin / admin)"
exec "$VENV_PYTHON" server.py
