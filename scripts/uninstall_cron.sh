#!/usr/bin/env bash
# Removes the daily crontab entry installed by install_cron.sh -- run this
# on your Mac if you've switched to the GitHub Actions daily-scan.yml
# schedule instead, to avoid getting two emails some mornings.
#   bash scripts/uninstall_cron.sh

set -euo pipefail

MARKER="# card-deal-scraper"
EXISTING="$(crontab -l 2>/dev/null || true)"

if ! echo "$EXISTING" | grep -q "$MARKER"; then
  echo "No card-deal-scraper cron entry found -- nothing to remove."
  exit 0
fi

echo "$EXISTING" | grep -v "$MARKER" | crontab -
echo "Removed the card-deal-scraper cron entry."
echo "Verify with: crontab -l"
