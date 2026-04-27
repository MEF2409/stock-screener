#!/usr/bin/env bash
# Installs the daily-refresh launchd job (Mac).
# Run: bash scripts/install-cron.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)/scripts/com.stock-screener.daily-refresh.plist"
DST="$HOME/Library/LaunchAgents/com.stock-screener.daily-refresh.plist"

cp "$SRC" "$DST"
launchctl unload "$DST" 2>/dev/null || true
launchctl load "$DST"

echo "✓ Installed. Job will run weekdays at 4:15pm local time."
echo "  Logs: $(cd "$(dirname "$0")/.." && pwd)/results/refresh.log"
echo "  Uninstall: launchctl unload $DST && rm $DST"
