#!/bin/sh
set -eu

if command -v chromium >/dev/null 2>&1; then
    browser=chromium
elif command -v chromium-browser >/dev/null 2>&1; then
    browser=chromium-browser
else
    echo "Chromium is not installed." >&2
    exit 1
fi

systemctl --user start bambudash.service
attempt=0
until curl -fsS http://127.0.0.1:8000/api/status >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "BambuDash service did not start." >&2
        exit 1
    fi
    sleep 1
done

if [ "${XDG_SESSION_TYPE:-}" = "x11" ] && command -v xset >/dev/null 2>&1; then
    xset s off -dpms s noblank || true
fi
exec "$browser" --kiosk --no-first-run --no-default-browser-check \
    --user-data-dir="${XDG_CACHE_HOME:-$HOME/.cache}/bambudash/chromium" \
    http://127.0.0.1:8000/touch.html