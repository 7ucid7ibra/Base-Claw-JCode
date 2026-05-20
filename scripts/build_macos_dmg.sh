#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v hdiutil >/dev/null 2>&1; then
  echo "hdiutil is required to build a macOS DMG."
  exit 1
fi

VERSION="${BASECLAW_DMG_VERSION:-}"
if [[ -z "$VERSION" ]]; then
  if command -v git >/dev/null 2>&1 && git rev-parse --short HEAD >/dev/null 2>&1; then
    VERSION="0.1.0-alpha+$(git rev-parse --short HEAD)"
  else
    VERSION="0.1.0-alpha"
  fi
fi

STAGE_ROOT="$ROOT_DIR/dist/macos-dmg-stage"
PAYLOAD_DIR="$STAGE_ROOT/BaseClaw"
DMG_PATH="$ROOT_DIR/dist/BaseClaw-${VERSION}.dmg"

rm -rf "$STAGE_ROOT"
mkdir -p "$PAYLOAD_DIR" "$ROOT_DIR/dist"

rsync -a --delete \
  --exclude ".git/" \
  --exclude ".venv*/" \
  --exclude "__pycache__/" \
  --exclude "agent_workspace/" \
  --exclude "profiles/" \
  --exclude "build/" \
  --exclude "dist/" \
  --exclude "tools/" \
  --exclude ".baseclaw-install.conf" \
  --exclude ".env" \
  --exclude ".env.*" \
  --exclude "*.log" \
  --exclude "*.out" \
  --exclude "*.pyc" \
  --exclude "*.pyo" \
  --exclude "*.sqlite3" \
  --exclude "*.sqlite3-*" \
  --exclude "telegram_operator_*.json" \
  --exclude "telegram_operator_*.jsonl" \
  "$ROOT_DIR/" "$PAYLOAD_DIR/"

BASECLAW_APP_VERSION="$VERSION" "$PAYLOAD_DIR/scripts/build_macos_app.sh" "$PAYLOAD_DIR/BaseClaw.app"

cat > "$STAGE_ROOT/README-FIRST.txt" <<'TXT'
BaseClaw macOS alpha package

1. Copy the BaseClaw folder somewhere writable, for example Applications or Documents.
2. Open BaseClaw/install-macos.command for first setup.
3. Open BaseClaw/start-macos.command or BaseClaw/BaseClaw.app for daily startup.

This alpha DMG is not signed or notarized yet. If macOS blocks it, Control-click the command or app and choose Open.
TXT

rm -f "$DMG_PATH"
hdiutil create \
  -volname "BaseClaw" \
  -srcfolder "$STAGE_ROOT" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Built $DMG_PATH"
