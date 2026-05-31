#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="${1:-$HOME/Applications/BaseClaw.app}"
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

python3 - "$MACOS_DIR/BaseClaw" "$ROOT_DIR" <<'PY'
from pathlib import Path
import shlex
import sys

target = Path(sys.argv[1])
root = sys.argv[2]
target.write_text(
    f"""#!/usr/bin/env bash
set -euo pipefail

BASECLAW_ROOT={shlex.quote(root)}

if [[ ! -x "$BASECLAW_ROOT/start.sh" ]]; then
  /usr/bin/osascript -e 'display dialog "BaseClaw could not find start.sh. Move the app back into the installed BaseClaw folder or reinstall the launcher." buttons {{"OK"}} default button "OK" with icon caution' >/dev/null 2>&1 || true
  exit 1
fi

cd "$BASECLAW_ROOT"
exec ./start.sh
""",
    encoding="utf-8",
)
PY

chmod +x "$MACOS_DIR/BaseClaw"

printf 'Installed BaseClaw launcher at %s\n' "$APP_DIR"
