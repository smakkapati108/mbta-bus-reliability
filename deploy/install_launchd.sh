#!/usr/bin/env bash
# Install macOS launchd jobs: the collector (always on, restarted if it dies),
# a refresh every 15 minutes, and a nightly GTFS check + compaction.
# Remove with: ./deploy/install_launchd.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/data/logs"
mkdir -p "$AGENTS" "$LOGS"

jobs=(collector refresh nightly)

if [[ "${1:-}" == "--uninstall" ]]; then
  for j in "${jobs[@]}"; do
    launchctl bootout "gui/$(id -u)/com.transit.$j" 2>/dev/null || true
    rm -f "$AGENTS/com.transit.$j.plist"
  done
  echo "uninstalled"; exit 0
fi

plist() {  # name, program-arguments-xml, schedule-xml
  cat > "$AGENTS/com.transit.$1.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.transit.$1</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key><array>$2</array>
  $3
  <key>EnvironmentVariables</key><dict>
    <key>MBTA_API_KEY</key><string>${MBTA_API_KEY:-}</string>
  </dict>
  <key>StandardOutPath</key><string>$LOGS/$1.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$1.log</string>
</dict></plist>
PLIST
}

plist collector "<string>$PY</string><string>-m</string><string>transit</string><string>collect</string>" \
  "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
plist refresh "<string>$PY</string><string>-m</string><string>transit</string><string>refresh</string>" \
  "<key>StartInterval</key><integer>900</integer>"
plist nightly "<string>/bin/sh</string><string>-c</string><string>$PY -m transit gtfs &amp;&amp; $PY -m transit compact</string>" \
  "<key>RunAtLoad</key><true/><key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>"

for j in "${jobs[@]}"; do
  launchctl bootout "gui/$(id -u)/com.transit.$j" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/com.transit.$j.plist"
done
echo "installed; logs in $LOGS"
echo "note: a sleeping Mac stops collecting. Run 'caffeinate -s' while plugged in, or use a server / GitHub Actions."
