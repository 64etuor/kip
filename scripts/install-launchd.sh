#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
interval="${1:-900}"
if ! [[ "$interval" =~ ^[0-9]+$ ]] || (( interval < 60 )); then
  echo "interval must be an integer >= 60 seconds" >&2
  exit 2
fi
agent_dir="$HOME/Library/LaunchAgents"
log_dir="$PROJECT_ROOT/var/log"
mkdir -p "$agent_dir" "$log_dir"

PROJECT_ROOT="$PROJECT_ROOT" INTERVAL="$interval" AGENT_DIR="$agent_dir" LOG_DIR="$log_dir" python3 - <<'PY'
import os
import plistlib
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"])
agent_dir = Path(os.environ["AGENT_DIR"])
log_dir = Path(os.environ["LOG_DIR"])
interval = int(os.environ["INTERVAL"])

items = {
    "com.kip.knowledge-fabric.worker": {
        "Label": "com.kip.knowledge-fabric.worker",
        "ProgramArguments": ["/bin/bash", str(root / "scripts/kip"), "worker", "run"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_dir / "launchd-worker.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-worker.err.log"),
    },
    "com.kip.knowledge-fabric.sync": {
        "Label": "com.kip.knowledge-fabric.sync",
        "ProgramArguments": ["/bin/bash", str(root / "scripts/kip"), "sync", "all", "--enqueue"],
        "WorkingDirectory": str(root),
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / "launchd-sync.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-sync.err.log"),
    },
}
for label, payload in items.items():
    with (agent_dir / f"{label}.plist").open("wb") as handle:
        plistlib.dump(payload, handle)
PY

for label in com.kip.knowledge-fabric.worker com.kip.knowledge-fabric.sync; do
  plist="$agent_dir/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
done
printf 'Installed KIP worker and %s-second sync scheduler.\n' "$interval"
