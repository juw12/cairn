#!/bin/bash
# Cairn for Mac: one-line install / update. No admin rights needed.
#
#   Paste this in Terminal (from https://juw12.github.io/cairn/):
#   curl -fsSL https://juw12.github.io/cairn/mac-install.sh | bash
#
# What it does, skipping anything already there:
#   1. Git (Apple's own; macOS offers to install it when it is missing)
#   2. A Python of Cairn's own (3.13, kept inside Cairn's folder; your other Pythons are left alone)
#   3. Cairn itself into ~/Library/Application Support/Cairn/app (a git checkout, so it can update itself)
#   4. The window and menu-bar libraries (pywebview, pystray, pillow, segno)
#   5. Cairn.app in ~/Applications, then opens it. Cairn keeps itself up to date from then on.
#
# To remove Cairn: delete ~/Applications/Cairn.app and ~/Library/Application Support/Cairn.
# Your notes are not in either of them.

set -euo pipefail

REPO="${CAIRN_REPO:-https://github.com/juw12/cairn.git}"
BRANCH="${CAIRN_BRANCH:-main}"
ROOT="${CAIRN_HOME:-$HOME/Library/Application Support/Cairn}"
BUNDLE="${CAIRN_APP_BUNDLE:-$HOME/Applications/Cairn.app}"
APP="$ROOT/app"
VENV="$ROOT/venv"
UV_DIR="$ROOT/uv"
PYTHON_VERSION="3.13"
LIBRARIES="pywebview pystray pillow segno"

say() { printf '  %s\n' "$1"; }
step() { printf '\n> %s\n' "$1"; }
stop() {
  printf '\nSetup did not finish: %s\n' "$1" >&2
  exit 1
}

[ "$(uname -s)" = "Darwin" ] || stop "this installer is for a Mac. On Windows use Cairn-Setup.cmd."
mkdir -p "$ROOT"

# ------------------------------------------------------------------ Git
has_git() { xcode-select -p >/dev/null 2>&1 && git --version >/dev/null 2>&1; }

step "Git (keeps your notes in sync and Cairn up to date)"
if ! has_git; then
  say "macOS is asking to install Apple's developer tools, which include Git."
  say "Click Install in that window. This continues by itself when it is done."
  xcode-select --install >/dev/null 2>&1 || true
  waited=0
  while ! has_git && [ "$waited" -lt 1800 ]; do
    sleep 5
    waited=$((waited + 5))
  done
fi
if has_git; then
  say "OK: $(git --version)"
else
  say "No Git yet. Cairn is installed without it: your notes stay on this Mac until Git is there,"
  say "and Cairn updates when you run this installer again."
fi

# ------------------------------------------------------------------ Python
step "Python (Cairn's own copy)"
export UV_PYTHON_INSTALL_DIR="$ROOT/python"
export UV_CACHE_DIR="$ROOT/uv-cache"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_PREFERENCE=only-managed
if [ ! -x "$UV_DIR/uv" ]; then
  say "Downloading the Python installer (uv)..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_DIR" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 \
    || stop "could not download uv from astral.sh. Check the internet connection and run this again."
fi
"$UV_DIR/uv" python install --quiet "$PYTHON_VERSION" || stop "could not download Python $PYTHON_VERSION."
PYTHON="$("$UV_DIR/uv" python find "$PYTHON_VERSION")"
[ -x "$PYTHON" ] || stop "Python $PYTHON_VERSION was downloaded but could not be found."
say "OK: $("$PYTHON" --version)"

# ------------------------------------------------------------------ Cairn
step "Cairn"
# A running Cairn keeps its port; it is opened again at the end.
pkill -f "$APP/Cairn.pyw" >/dev/null 2>&1 || true
if has_git && [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only --quiet || stop "could not update Cairn from GitHub."
  say "Updated to the latest version"
elif has_git; then
  rm -rf "$APP"
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP" || stop "could not download Cairn from GitHub."
  say "Downloaded"
else
  rm -rf "$APP" "$ROOT/app-download"
  mkdir -p "$ROOT/app-download"
  curl -fsSL "${REPO%.git}/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$ROOT/app-download" --strip-components 1 \
    || stop "could not download Cairn from GitHub."
  mv "$ROOT/app-download" "$APP"
  say "Downloaded (without Git)"
fi

step "Window and menu-bar libraries"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import sys; sys.exit(sys.version_info < (3, 13))" 2>/dev/null; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV" || stop "could not set up Cairn's Python."
fi
# shellcheck disable=SC2086
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade $LIBRARIES \
  || stop "could not install the window libraries (pip failed)."
rm -rf "$UV_CACHE_DIR"
say "OK"

# ------------------------------------------------------------------ Cairn.app
step "Cairn.app"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cat >"$BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Cairn</string>
  <key>CFBundleDisplayName</key><string>Cairn</string>
  <key>CFBundleIdentifier</key><string>io.github.juw12.cairn</string>
  <key>CFBundleExecutable</key><string>Cairn</string>
  <key>CFBundleIconFile</key><string>Cairn</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Cairn listens when you dictate a note or a voice log.</string>
</dict>
</plist>
PLIST
# The launcher: Cairn's Python runs the checkout. The paths are written in, so the app can be moved.
cat >"$BUNDLE/Contents/MacOS/Cairn" <<LAUNCHER
#!/bin/bash
export CAIRN_APP_BUNDLE="\$(cd "\$(dirname "\$0")/../.." && pwd)"
export PATH="\$PATH:/usr/local/bin:/opt/homebrew/bin"
cd "$APP" || exit 1
exec "$VENV/bin/python" "$APP/Cairn.pyw" "\$@"
LAUNCHER
chmod +x "$BUNDLE/Contents/MacOS/Cairn"
# The icon, in the shape macOS gives its apps. Without it Cairn still works, with a plain icon.
"$VENV/bin/python" "$APP/scripts/mac_icon.py" "$APP/docs/icons/icon-512.png" "$BUNDLE/Contents/Resources/Cairn.icns" \
  >/dev/null 2>&1 || say "(the icon could not be made; Cairn works without it)"
touch "$BUNDLE"
say "Added Cairn to $(dirname "$BUNDLE")"

if [ "${CAIRN_NO_OPEN:-}" = "1" ]; then
  printf '\nDone. Open Cairn from %s.\n' "$BUNDLE"
  exit 0
fi
step "Opening Cairn"
open "$BUNDLE"
printf '\nDone. Cairn is open. Closing its window keeps it in the menu bar; click the Dock icon to bring it back.\n'
printf 'Everything else (opening your notes, syncing, settings) is inside the app.\n'
