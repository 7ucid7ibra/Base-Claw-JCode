#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR=".venv-kokoro"
PYTHON="$VENV_DIR/bin/python"
WHISPER_VENV_DIR=".venv-whisper"
WHISPER_PYTHON="$WHISPER_VENV_DIR/bin/python"
PID_FILE="kokoro_server.pid"
LOG_FILE="kokoro_server.log"
URL="${BASECLAW_SPEECH_URL:-http://127.0.0.1:8766}"

say() {
  printf '%s\n' "$*"
}

is_running() {
  curl -fsS "$URL/health" >/dev/null 2>&1
}

install_server() {
  local py=""
  for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      py="$(command -v "$candidate")"
      break
    fi
  done
  if [[ -z "$py" ]]; then
    say "Python 3 was not found."
    exit 1
  fi
  if [[ ! -d "$VENV_DIR" ]]; then
    "$py" -m venv "$VENV_DIR"
  fi
  "$PYTHON" -m pip install --upgrade pip wheel
  "$PYTHON" -m pip install -r requirements/kokoro.txt
  if [[ ! -d "$WHISPER_VENV_DIR" ]]; then
    "$py" -m venv "$WHISPER_VENV_DIR"
  fi
  "$WHISPER_PYTHON" -m pip install --upgrade pip wheel
  "$WHISPER_PYTHON" -m pip install -r requirements/whisper.txt
  if [[ -f ".baseclaw-install.conf" ]] && grep -q '^BASECLAW_WITH_KOKORO=' ".baseclaw-install.conf"; then
    "$py" - <<'PY'
from pathlib import Path
path = Path(".baseclaw-install.conf")
lines = path.read_text(encoding="utf-8").splitlines()
lines = ["BASECLAW_WITH_KOKORO=1" if line.startswith("BASECLAW_WITH_KOKORO=") else line for line in lines]
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  else
    printf '\nBASECLAW_WITH_KOKORO=1\n' >> ".baseclaw-install.conf"
  fi
  say "Local speech support installed."
}

start_server() {
  if is_running; then
    say "Speech server is already running at $URL."
    return
  fi
  if [[ ! -x "$PYTHON" || ! -x "$WHISPER_PYTHON" ]]; then
    say "Speech server is not installed. Run: scripts/speech_server.sh install"
    exit 1
  fi
  nohup "$PYTHON" app/speech/server.py >> "$LOG_FILE" 2>&1 &
  printf '%s\n' "$!" > "$PID_FILE"
  for _ in $(seq 1 45); do
    if is_running; then
      say "Speech server started at $URL."
      return
    fi
    sleep 1
  done
  say "Timed out waiting for speech server at $URL."
  exit 1
}

stop_server() {
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      sleep 1
    fi
    rm -f "$PID_FILE"
  fi
  pkill -f "app/speech/server.py" >/dev/null 2>&1 || true
  if is_running; then
    say "Speech server still appears to be running at $URL."
    exit 1
  fi
  say "Speech server stopped."
}

case "${1:-status}" in
  install) install_server ;;
  start) start_server ;;
  stop) stop_server ;;
  restart)
    stop_server
    start_server
    ;;
  status)
    if is_running; then
      say "running $URL"
    elif [[ -x "$PYTHON" && -x "$WHISPER_PYTHON" ]]; then
      say "stopped"
    else
      say "not installed"
    fi
    ;;
  *)
    say "Usage: scripts/speech_server.sh install|start|stop|restart|status"
    exit 2
    ;;
esac
