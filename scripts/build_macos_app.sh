#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${1:-$ROOT_DIR/dist/BaseClaw.app}"
APP_VERSION="${BASECLAW_APP_VERSION:-0.1.0}"
MACOS_DIR="$APP_DIR/Contents/MacOS"

mkdir -p "$MACOS_DIR"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>BaseClaw</string>
  <key>CFBundleDisplayName</key>
  <string>BaseClaw</string>
  <key>CFBundleIdentifier</key>
  <string>com.baseclaw.app</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>__BASECLAW_APP_VERSION__</string>
  <key>CFBundleExecutable</key>
  <string>BaseClaw</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

python3 - "$APP_DIR/Contents/Info.plist" "$APP_VERSION" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
path.write_text(path.read_text().replace("__BASECLAW_APP_VERSION__", version), encoding="utf-8")
PY

cat > "$MACOS_DIR/BaseClaw" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

APP_EXEC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for candidate in "$APP_EXEC/../../.." "$APP_EXEC/../../../.."; do
  candidate="$(cd "$candidate" && pwd)"
  if [[ -x "$candidate/start.sh" ]]; then
    cd "$candidate"
    exec ./start.sh
  fi
done

echo "BaseClaw.app could not find start.sh next to the application bundle."
echo "Use install-macos.command for first setup, then start-macos.command for daily startup."
read -r -p "Press Return to close. " _
exit 1
SH

chmod +x "$MACOS_DIR/BaseClaw"

printf 'Built %s\n' "$APP_DIR"
