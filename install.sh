#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

YES=0
LAUNCH=1
WITH_KOKORO=""

for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    --no-launch) LAUNCH=0 ;;
    --with-kokoro) WITH_KOKORO=1 ;;
    --without-kokoro) WITH_KOKORO=0 ;;
    -h|--help)
      cat <<'EOF'
BaseClaw installer and launcher

Usage:
  ./install.sh [--yes] [--with-kokoro|--without-kokoro] [--no-launch]

What it does:
  - creates/updates the Python UI/operator virtual environment
  - optionally installs Codex CLI, Claude CLI, and JCode
  - checks LM Studio and Ollama availability
  - optionally creates/updates the Kokoro voice server virtual environment
  - creates .env.telegram-operator from the example if missing
  - launches the UI unless --no-launch is passed
EOF
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() {
  printf '\n%s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

ask() {
  local prompt="$1"
  local default="${2:-y}"
  if [[ "$YES" == "1" ]]; then
    [[ "$default" =~ ^[Yy]$ ]]
    return
  fi
  local suffix="[y/N]"
  [[ "$default" =~ ^[Yy]$ ]] && suffix="[Y/n]"
  local reply
  read -r -p "$prompt $suffix " reply
  reply="${reply:-$default}"
  [[ "$reply" =~ ^[Yy]$ ]]
}

python_bin() {
  if have python3.12; then
    command -v python3.12
  elif have python3.11; then
    command -v python3.11
  elif have python3; then
    command -v python3
  else
    echo ""
  fi
}

ensure_node_tool() {
  local binary="$1"
  local package="$2"
  local label="$3"
  if have "$binary"; then
    say "$label found: $(command -v "$binary")"
    return
  fi
  if ! have npm; then
    say "$label not found, and npm is not installed. Skipping."
    return
  fi
  if ask "Install $label globally with npm?" "y"; then
    npm install -g "$package"
  else
    say "Skipped $label."
  fi
}

ensure_jcode() {
  if have jcode; then
    say "JCode found: $(command -v jcode)"
    return
  fi
  if ! have brew; then
    say "JCode not found, and Homebrew is not installed. Skipping."
    return
  fi
  if ask "Install JCode with Homebrew?" "y"; then
    brew tap 1jehuang/jcode || true
    brew install 1jehuang/jcode/jcode
  else
    say "Skipped JCode."
  fi
}

check_lm_studio() {
  if curl -fsS --max-time 2 "http://127.0.0.1:1234/v1/models" >/dev/null 2>&1; then
    say "LM Studio server is reachable on port 1234."
    return
  fi
  if [[ -d "/Applications/LM Studio.app" ]]; then
    say "LM Studio app is installed, but the local server is not reachable on port 1234."
    say "Open LM Studio, load a model, and start the local server if you want JCode/local models."
    return
  fi
  say "LM Studio was not detected. Install it manually if you want LM Studio local models."
}

ensure_ollama() {
  if have ollama; then
    say "Ollama found: $(command -v ollama)"
    return
  fi
  if have brew && ask "Install Ollama with Homebrew?" "n"; then
    brew install ollama
  else
    say "Ollama not installed. Skipping."
  fi
}

setup_venv() {
  local venv="$1"
  local requirements="$2"
  local py
  py="$(python_bin)"
  if [[ -z "$py" ]]; then
    say "Python 3 was not found. Install Python 3.11+ first."
    exit 1
  fi
  if [[ ! -d "$venv" ]]; then
    "$py" -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --upgrade pip wheel
  "$venv/bin/python" -m pip install -r "$requirements"
}

setup_kokoro() {
  if [[ "$WITH_KOKORO" == "0" ]]; then
    say "Skipping Kokoro setup."
    return
  fi
  if [[ "$WITH_KOKORO" == "1" ]] || ask "Set up local Kokoro voice server dependencies?" "n"; then
    setup_venv ".venv-kokoro" "requirements/kokoro.txt"
    say "Kokoro dependencies are installed. You can start the server with:"
    say "  .venv-kokoro/bin/python app/kokoro_server.py"
  else
    say "Skipped Kokoro setup."
  fi
}

say "BaseClaw setup"

if [[ ! -f ".env.telegram-operator" && -f ".env.telegram-operator.example" ]]; then
  cp ".env.telegram-operator.example" ".env.telegram-operator"
  say "Created .env.telegram-operator from the example. Open settings to enter your bot token and chat id."
fi

say "Setting up Python UI/operator environment..."
setup_venv ".venv-telegram-agent" "requirements/telegram-operator.txt"

say "Checking optional coding providers..."
ensure_node_tool "codex" "@openai/codex" "Codex CLI"
ensure_node_tool "claude" "@anthropic-ai/claude-code" "Claude CLI"
ensure_jcode

say "Checking local model tooling..."
check_lm_studio
ensure_ollama

say "Checking optional voice tooling..."
setup_kokoro

say "Setup complete."

if [[ "$LAUNCH" == "1" ]]; then
  say "Launching BaseClaw UI..."
  exec ".venv-telegram-agent/bin/python" "app/telegram_operator_ui.py"
else
  say "Launch skipped. Start later with: ./install.sh"
fi
