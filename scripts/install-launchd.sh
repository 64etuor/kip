#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<'USAGE'
usage: install-launchd.sh [INTERVAL] [options]

  INTERVAL              sync interval in seconds (default 900, minimum 60)
  --retain N            daily backup retention count passed to backup.sh
                        (default 7)
  --backup-hour H       hour (0-23, local time) for the daily backup
                        (default 3)
  --with-ops-report     also install a periodic ops-report launchd item
  --ops-interval N      ops-report interval in seconds (default 1800,
                        minimum 300; implies --with-ops-report)
  --allow-compose-worker
                        proceed even when a Docker Compose worker service is
                        already running (risks double processing)
  --dry-run             render plists under var/launchd-preview/ and print
                        the launchctl commands without installing anything
USAGE
}

interval=900
retain=7
backup_hour=3
with_ops_report=0
ops_interval=1800
allow_compose_worker=0
dry_run=0
positional_seen=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --retain) retain="${2:-}"; shift 2 ;;
    --retain=*) retain="${1#--retain=}"; shift ;;
    --backup-hour) backup_hour="${2:-}"; shift 2 ;;
    --backup-hour=*) backup_hour="${1#--backup-hour=}"; shift ;;
    --with-ops-report) with_ops_report=1; shift ;;
    --ops-interval) with_ops_report=1; ops_interval="${2:-}"; shift 2 ;;
    --ops-interval=*) with_ops_report=1; ops_interval="${1#--ops-interval=}"; shift ;;
    --allow-compose-worker) allow_compose_worker=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --*) printf 'unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    *)
      if (( positional_seen )); then
        printf 'unexpected argument: %s\n' "$1" >&2; usage >&2; exit 2
      fi
      interval="$1"; positional_seen=1; shift ;;
  esac
done

if ! [[ "$interval" =~ ^[0-9]+$ ]] || (( interval < 60 )); then
  echo "interval must be an integer >= 60 seconds" >&2
  exit 2
fi
if ! [[ "$retain" =~ ^[0-9]+$ ]] || (( retain < 1 )); then
  echo "--retain must be an integer >= 1" >&2
  exit 2
fi
if ! [[ "$backup_hour" =~ ^[0-9]+$ ]] || (( backup_hour > 23 )); then
  echo "--backup-hour must be an integer between 0 and 23" >&2
  exit 2
fi
if ! [[ "$ops_interval" =~ ^[0-9]+$ ]] || (( ops_interval < 300 )); then
  echo "--ops-interval must be an integer >= 300 seconds" >&2
  exit 2
fi

# Guard against double workers: the launchd host worker and a Docker Compose
# worker service consuming the same queue means duplicated processing.
compose_worker="$(cd "$PROJECT_ROOT" && docker compose ps --services --status running 2>/dev/null | grep -x worker || true)"
if [[ -n "$compose_worker" ]]; then
  if (( allow_compose_worker )); then
    echo "WARNING: a Docker Compose worker service is running; installing the launchd worker anyway (--allow-compose-worker)." >&2
  else
    echo "REFUSING: a Docker Compose worker service is already running." >&2
    echo "Stop it (docker compose stop worker) or pass --allow-compose-worker." >&2
    exit 2
  fi
fi

agent_dir="$HOME/Library/LaunchAgents"
log_dir="$PROJECT_ROOT/var/log"
if (( dry_run )); then
  agent_dir="$PROJECT_ROOT/var/launchd-preview"
fi
mkdir -p "$agent_dir" "$log_dir"

PROJECT_ROOT="$PROJECT_ROOT" INTERVAL="$interval" AGENT_DIR="$agent_dir" \
LOG_DIR="$log_dir" RETAIN="$retain" BACKUP_HOUR="$backup_hour" \
WITH_OPS_REPORT="$with_ops_report" OPS_INTERVAL="$ops_interval" python3 - <<'PY'
import os
import plistlib
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"])
agent_dir = Path(os.environ["AGENT_DIR"])
log_dir = Path(os.environ["LOG_DIR"])
interval = int(os.environ["INTERVAL"])
retain = int(os.environ["RETAIN"])
backup_hour = int(os.environ["BACKUP_HOUR"])
ops_interval = int(os.environ["OPS_INTERVAL"])

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
    "com.kip.backup": {
        "Label": "com.kip.backup",
        "ProgramArguments": [
            "/bin/bash",
            str(root / "scripts/backup.sh"),
            "--retain",
            str(retain),
        ],
        "WorkingDirectory": str(root),
        "RunAtLoad": False,
        "StartCalendarInterval": {"Hour": backup_hour, "Minute": 15},
        "StandardOutPath": str(log_dir / "launchd-backup.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-backup.err.log"),
    },
}
if os.environ["WITH_OPS_REPORT"] == "1":
    items["com.kip.ops-report"] = {
        "Label": "com.kip.ops-report",
        "ProgramArguments": ["/bin/bash", str(root / "scripts/ops-report.sh")],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "StartInterval": ops_interval,
        "StandardOutPath": str(log_dir / "launchd-ops-report.out.log"),
        "StandardErrorPath": str(log_dir / "launchd-ops-report.err.log"),
    }
for label, payload in items.items():
    with (agent_dir / f"{label}.plist").open("wb") as handle:
        plistlib.dump(payload, handle)
print("\n".join(sorted(items)))
PY

labels=(com.kip.knowledge-fabric.worker com.kip.knowledge-fabric.sync com.kip.backup)
if (( with_ops_report )); then
  labels+=(com.kip.ops-report)
fi

# Log rotation: launchd appends to var/log/launchd-*.log forever. Generate a
# newsyslog(8) configuration; installing it into /etc/newsyslog.d requires
# sudo, so print the command instead of running it.
newsyslog_conf="$PROJECT_ROOT/var/newsyslog.kip.conf"
{
  printf '# newsyslog.d configuration for KIP launchd job logs.\n'
  printf '# Install with: sudo install -m 644 %q /etc/newsyslog.d/kip.conf\n' "$newsyslog_conf"
  printf '# logfilename                                     [owner:group]  mode count size(KB) when flags\n'
  for stem in worker sync backup ops-report; do
    for stream in out err; do
      printf '%s  %s  644  5  10240  *  J\n' \
        "$log_dir/launchd-$stem.$stream.log" "$(id -un):staff"
    done
  done
} > "$newsyslog_conf"

if (( dry_run )); then
  printf 'DRY RUN: rendered plists under %s (nothing installed):\n' "$agent_dir"
  for label in "${labels[@]}"; do
    printf '  %s.plist\n' "$label"
    printf '  would run: launchctl bootout gui/%s/%s (ignore failure)\n' "$(id -u)" "$label"
    printf '  would run: launchctl bootstrap gui/%s %s\n' "$(id -u)" "$agent_dir/$label.plist"
  done
  printf 'DRY RUN: log rotation config rendered at %s\n' "$newsyslog_conf"
  printf 'DRY RUN: to enable rotation run: sudo install -m 644 %q /etc/newsyslog.d/kip.conf\n' "$newsyslog_conf"
  exit 0
fi

for label in "${labels[@]}"; do
  plist="$agent_dir/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
done
printf 'Installed KIP worker, %s-second sync scheduler, and daily %02d:15 backup (retain %s).\n' \
  "$interval" "$backup_hour" "$retain"
if (( with_ops_report )); then
  printf 'Installed com.kip.ops-report every %s seconds.\n' "$ops_interval"
fi
printf 'Enable log rotation with: sudo install -m 644 %q /etc/newsyslog.d/kip.conf\n' "$newsyslog_conf"
