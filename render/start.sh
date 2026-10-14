#!/bin/sh
set -eu

if [ -z "${SCRAPER_API_KEY:-}" ]; then
  echo "SCRAPER_API_KEY is required" >&2
  exit 1
fi

DATA_DIR="${SCRAPER_DATA_DIR:-/tmp/gmapsdata}"
mkdir -p "$DATA_DIR"

google-maps-scraper \
  -web \
  -addr 127.0.0.1:8080 \
  -data-folder "$DATA_DIR" &

SCRAPER_PID=$!

cleanup() {
  kill "$SCRAPER_PID" 2>/dev/null || true
  wait "$SCRAPER_PID" 2>/dev/null || true
}

trap cleanup INT TERM EXIT

exec python3 /opt/exporadar/proxy.py
