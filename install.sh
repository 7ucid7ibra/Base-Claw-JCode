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

python_has_tkinter() {
  "$1" - <<'PY' >/dev/null 2>&1
import tkinter
PY
}

python_bin() {
  local fallback=""
  local name path
  for name in python3.12 python3.11 python3; do
    if have "$name"; then
      path="$(command -v "$name")"
      [[ -z "$fallback" ]] && fallback="$path"
      if python_has_tkinter "$path"; then
        echo "$path"
        return
      fi
    fi
  done
  echo "$fallback"
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
  if have brew && ask "Install JCode with Homebrew?" "y"; then
    brew tap 1jehuang/jcode || true
    if brew install 1jehuang/jcode/jcode; then
      return
    fi
    say "Homebrew could not install JCode. Trying direct release download instead."
    install_jcode_release || say "Direct JCode install failed. You can still use Codex or Claude."
    return
  fi
  if ask "Install JCode from the GitHub release into ~/.local/bin?" "y"; then
    install_jcode_release || say "Direct JCode install failed. You can still use Codex or Claude."
  else
    say "Skipped JCode."
  fi
}

install_jcode_release() {
  local version="0.12.2"
  local os arch asset bin_name url tmpdir target_dir target
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os:$arch" in
    Darwin:arm64) asset="jcode-macos-aarch64.tar.gz"; bin_name="jcode-macos-aarch64" ;;
    Darwin:x86_64) asset="jcode-macos-x86_64.tar.gz"; bin_name="jcode-macos-x86_64" ;;
    Linux:aarch64|Linux:arm64) asset="jcode-linux-aarch64.tar.gz"; bin_name="jcode-linux-aarch64" ;;
    Linux:x86_64) asset="jcode-linux-x86_64.tar.gz"; bin_name="jcode-linux-x86_64" ;;
    *)
      say "No direct JCode release mapping for $os/$arch."
      return 1
      ;;
  esac
  if ! have curl; then
    say "curl is required for direct JCode install."
    return 1
  fi
  if ! have tar; then
    say "tar is required for direct JCode install."
    return 1
  fi
  tmpdir="$(mktemp -d)"
  url="https://github.com/1jehuang/jcode/releases/download/v${version}/${asset}"
  say "Downloading JCode $version for $os/$arch..."
  curl -fL "$url" -o "$tmpdir/$asset"
  tar -xzf "$tmpdir/$asset" -C "$tmpdir"
  target_dir="$HOME/.local/bin"
  mkdir -p "$target_dir"
  target="$target_dir/jcode"
  install -m 755 "$tmpdir/$bin_name" "$target"
  rm -rf "$tmpdir"
  if ! have jcode; then
    say "Installed JCode to $target."
    say "Add this to PATH if needed: export PATH=\"\$HOME/.local/bin:\$PATH\""
  else
    say "JCode installed: $(command -v jcode)"
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
  if [[ "$venv" == ".venv-telegram-agent" ]]; then
    ensure_tkinter "$py"
  fi
  if [[ ! -d "$venv" ]]; then
    "$py" -m venv "$venv"
  fi
  if [[ "$venv" == ".venv-telegram-agent" ]] && ! python_has_tkinter "$venv/bin/python"; then
    say "Recreating UI/operator virtual environment so it can see Tkinter."
    rm -rf "$venv"
    "$py" -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --upgrade pip wheel
  "$venv/bin/python" -m pip install -r "$requirements"
}

ensure_tkinter() {
  local py="$1"
  if python_has_tkinter "$py"; then
    return
  fi
  say "Tkinter is not available for $("$py" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')."
  if [[ "$(uname -s)" == "Darwin" ]] && have brew; then
    local minor package
    minor="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    package="python-tk@${minor}"
    if ask "Install $package with Homebrew so the desktop UI can launch?" "y"; then
      brew install "$package"
    fi
  fi
  if python_has_tkinter "$py"; then
    return
  fi
  say "Tkinter is still unavailable. Install a Python build with Tkinter support, then rerun ./install.sh."
  exit 1
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
