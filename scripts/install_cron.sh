#!/usr/bin/env bash
# Installs (or updates) the daily crontab entry for the card deal scraper.
# Run this ON YOUR MAC, from anywhere:
#   bash scripts/install_cron.sh [HOUR] [MINUTE]
# Defaults to 8:00am local time if no args given.

set -euo pipefail

HOUR="${1:-8}"
MINUTE="${2:-0}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
else
  PYTHON_BIN="$(command -v python3)"
fi

MARKER="# card-deal-scraper"
CRON_LINE="$MINUTE $HOUR * * * cd \"$PROJECT_DIR\" && \"$PYTHON_BIN\" -m src.main >> \"$PROJECT_DIR/logs/cron.log\" 2>&1 $MARKER"

EXISTING="$(crontab -l 2>/dev/null || true)"

if echo "$EXISTING" | grep -q "$MARKER"; then
  echo "Existing card-deal-scraper cron entry found -- replacing it."
  NEW_CRONTAB="$(echo "$EXISTING" | grep -v "$MARKER")"
else
  NEW_CRONTAB="$EXISTING"
fi

{ echo "$NEW_CRONTAB"; echo "$CRON_LINE"; } | grep -v '^$' | crontab -

echo "Installed cron job:"
echo "  $CRON_LINE"
echo ""
echo "Verify with: crontab -l"
echo "Logs will append to: $PROJECT_DIR/logs/cron.log"
