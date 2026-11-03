#!/usr/bin/env bash
# Opens the dashboard full-screen once the local service is up.
URL="http://127.0.0.1:8080/rotate"

# Wait up to 60s for the dashboard service.
for _ in $(seq 1 60); do
  curl -fs "http://127.0.0.1:8080/api/status" >/dev/null 2>&1 && break
  sleep 1
done

BROWSER=$(command -v chromium || command -v chromium-browser)

exec "$BROWSER" \
  --kiosk "$URL" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=TranslateUI \
  --check-for-update-interval=31536000 \
  --password-store=basic \
  --incognito
