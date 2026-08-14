#!/usr/bin/env bash
set -euo pipefail
for label in com.kip.knowledge-fabric.worker com.kip.knowledge-fabric.sync com.kip.backup com.kip.ops-report; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$plist"
done
printf 'Removed KIP launch agents.\n'
printf 'If installed, remove log rotation with: sudo rm -f /etc/newsyslog.d/kip.conf\n'
