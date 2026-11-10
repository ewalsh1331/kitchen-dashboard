#!/usr/bin/env bash
# Screenshots for checking the kitchen screen. Read-only: never changes anything.
#   bash deploy/screenshot.sh screen [OUT.png] [--wait SECONDS]
#       what the kitchen monitor shows right now
#   bash deploy/screenshot.sh page FILE.html OUT.png [--wait SECONDS] [--size WxH]
#       render a local mockup file off-screen at the monitor's size (default 1920x1080 visible area)
#       (live dashboard pages don't render off-screen on this Pi; use 'screen' for those)
# Default output folder: data/screens/ (not committed).
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/screens

WAIT=0; SIZE="1920x1080"; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
MODE="${ARGS[0]:-screen}"
[ "$WAIT" != "0" ] && sleep "$WAIT"

case "$MODE" in
  screen)
    OUT="${ARGS[1]:-data/screens/screen-$(date +%H%M%S).png}"
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" XDG_RUNTIME_DIR="/run/user/$(id -u)" grim "$OUT" || exit 1
    ;;
  page)
    SRC="${ARGS[1]:?usage: screenshot.sh page FILE.html OUT.png}"
    OUT="${ARGS[2]:?usage: screenshot.sh page FILE.html OUT.png}"
    [ -f "$SRC" ] || { echo "Not a file: $SRC (for the live screen use: screenshot.sh screen)"; exit 1; }
    SRC="file://$(realpath "$SRC")"
    W="${SIZE%x*}"; H="${SIZE#*x}"
    # Headless Chromium's visible area is ~143px shorter than the window; pad so the page sees $H.
    PROFILE="$(mktemp -d)"
    timeout 60 /usr/lib/chromium/chromium --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
      --no-first-run --hide-scrollbars --user-data-dir="$PROFILE" --window-size="$W,$((H + 143))" \
      --screenshot="$OUT" "$SRC" >/dev/null 2>&1
    rm -rf "$PROFILE"
    [ -s "$OUT" ] || { echo "Render failed: $SRC"; exit 1; }
    ;;
  *) sed -n 2,7p "$0"; exit 1 ;;
esac
echo "$OUT"
