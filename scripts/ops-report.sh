#!/usr/bin/env bash
# Single-command operational health report.
#
#   ops-report.sh [--json]
#
# Checks: failed/queued jobs, last successful sync progress, disk free,
# newest backup age, and API health. Exits nonzero with a summary line when
# any check breaches its threshold. With --json, prints a machine-readable
# report instead of text. When failing and KIP_OPS_WEBHOOK is set, POSTs the
# JSON report to that URL.
#
# Thresholds (environment, all optional):
#   KIP_OPS_MAX_QUEUE_AGE_SECONDS  oldest queued job age limit (default 3600)
#   KIP_OPS_MAX_SYNC_AGE_SECONDS   last sync progress age limit (default: warn only)
#   KIP_OPS_DISK_MIN_FREE_PCT      minimum free percentage on var/ (default 10)
#   KIP_OPS_BACKUP_MAX_AGE_HOURS   newest sealed backup age limit (default 26)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/postgres-tools.sh"
cd "$PROJECT_ROOT"

OUTPUT_MODE="text"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) OUTPUT_MODE="json"; shift ;;
    -h|--help)
      sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) printf 'unknown argument: %s\nusage: ops-report.sh [--json]\n' "$1" >&2; exit 2 ;;
  esac
done

PY="$(python_cmd)"
KIP_CLI="${KIP_CLI:-$SCRIPT_DIR/kip}"
BACKUP_ROOT="${KIP_BACKUP_PATH:-$PROJECT_ROOT/var/backups}"
if [[ -d "$BACKUP_ROOT" ]]; then
  BACKUP_ROOT="$(cd "$BACKUP_ROOT" && pwd -P)"
fi
RUN_DIR="$PROJECT_ROOT/var/run"
mkdir -p "$RUN_DIR"
REPORT_JSON="$RUN_DIR/ops-report-last.json"

# --- jobs: failed count via the CLI JSON envelope --------------------------
FAILED_JOBS_JSON="$("$KIP_CLI" jobs list --status failed --limit 1000 2>/dev/null || true)"

# --- database probes (read-only) -------------------------------------------
DATABASE_URL="$("$PY" "$SCRIPT_DIR/secret_value.py" KIP_DATABASE_URL 2>/dev/null || true)"
OLDEST_QUEUED_SECONDS=""
SYNC_ROW=""
if [[ -n "$DATABASE_URL" ]]; then
  OLDEST_QUEUED_SECONDS="$(postgres_query "$DATABASE_URL" \
    "SELECT COALESCE(extract(epoch FROM now() - min(created_at))::bigint, -1) FROM jobs.queue WHERE status = 'queued'" \
    2>/dev/null | tr -d '[:space:]' || true)"
  # Last successful sync progress: newest of the connector cursor updates and
  # the newest source-object sighting (filesystem sync updates last_seen_at on
  # every successful scan even when it stores no cursor).
  SYNC_ROW="$(postgres_query "$DATABASE_URL" \
    "WITH progress AS (SELECT GREATEST((SELECT max(updated_at) FROM source.sync_cursors), (SELECT max(last_seen_at) FROM source.objects)) AS at) SELECT COALESCE(extract(epoch FROM now() - at)::bigint, -1), COALESCE(to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), '') FROM progress" \
    2>/dev/null | head -1 | tr -d '[:space:]' || true)"
fi

# --- disk free ---------------------------------------------------------------
VAR_DF="$(df -Pk "$PROJECT_ROOT/var" 2>/dev/null | awk 'NR==2 {print $4"|"$5"|"$6}' || true)"
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
DOCKER_DF=""
if [[ -n "$DOCKER_ROOT" && -d "$DOCKER_ROOT" ]]; then
  DOCKER_DF="$(df -Pk "$DOCKER_ROOT" 2>/dev/null | awk 'NR==2 {print $4"|"$5"|"$6}' || true)"
fi

# --- newest sealed backup ----------------------------------------------------
NEWEST_BACKUP=""
if [[ -d "$BACKUP_ROOT" ]]; then
  while IFS= read -r entry; do
    name="$(basename "$entry")"
    if [[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z$ && -f "$entry/backup-manifest.json" ]]; then
      NEWEST_BACKUP="$entry"
    fi
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | sort)
fi

# --- API health --------------------------------------------------------------
# Probe /readyz first: it performs a real database round-trip, so a wedged
# database surfaces as an API failure. Older deployments without /readyz
# answer 404; fall back to the liveness-only /healthz for those.
API_BASE="http://127.0.0.1:${KIP_API_PORT:-8080}"
API_URL="$API_BASE/readyz"
API_STATE="not_running"
API_HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$API_URL" 2>/dev/null || true)"
if [[ "$API_HTTP_CODE" == "404" ]]; then
  API_URL="$API_BASE/healthz"
  API_HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$API_URL" 2>/dev/null || true)"
fi
if [[ "$API_HTTP_CODE" == "200" ]]; then
  API_STATE="healthy"
elif [[ -n "$API_HTTP_CODE" && "$API_HTTP_CODE" != "000" ]]; then
  API_STATE="unhealthy"
fi

set +e
FAILED_JOBS_JSON="$FAILED_JOBS_JSON" \
OLDEST_QUEUED_SECONDS="$OLDEST_QUEUED_SECONDS" \
SYNC_ROW="$SYNC_ROW" \
VAR_DF="$VAR_DF" \
DOCKER_ROOT="$DOCKER_ROOT" \
DOCKER_DF="$DOCKER_DF" \
NEWEST_BACKUP="$NEWEST_BACKUP" \
API_STATE="$API_STATE" \
API_URL="$API_URL" \
OUTPUT_MODE="$OUTPUT_MODE" \
REPORT_JSON="$REPORT_JSON" \
BACKUP_ROOT="$BACKUP_ROOT" \
"$PY" - <<'PY'
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

max_queue_age = int(os.environ.get("KIP_OPS_MAX_QUEUE_AGE_SECONDS", "3600"))
max_sync_age = os.environ.get("KIP_OPS_MAX_SYNC_AGE_SECONDS")
min_free_pct = int(os.environ.get("KIP_OPS_DISK_MIN_FREE_PCT", "10"))
max_backup_age_hours = float(os.environ.get("KIP_OPS_BACKUP_MAX_AGE_HOURS", "26"))

checks: dict[str, dict] = {}
failures: list[str] = []
warnings: list[str] = []


def record(name: str, ok: bool, detail: dict, failure: str | None = None) -> None:
    checks[name] = {"ok": ok, **detail}
    if not ok and failure:
        failures.append(failure)


# failed jobs -----------------------------------------------------------------
raw = os.environ["FAILED_JOBS_JSON"].strip()
if raw:
    try:
        envelope = json.loads(raw)
        jobs = envelope.get("data") or []
        count = len(jobs) if isinstance(jobs, list) else -1
    except json.JSONDecodeError:
        count = -1
else:
    count = -1
if count < 0:
    record("failed_jobs", False, {"count": None, "error": "jobs list unavailable"},
           "failed-jobs check unavailable (CLI/database unreachable)")
else:
    record("failed_jobs", count == 0, {"count": count},
           f"{count} failed job(s) in the queue" if count else None)

# oldest queued job -----------------------------------------------------------
raw = os.environ["OLDEST_QUEUED_SECONDS"]
if raw in ("", None):
    record("queue_age", False, {"oldest_seconds": None, "error": "database unreachable"},
           "queue-age check unavailable (database unreachable)")
else:
    age = int(raw)
    if age < 0:
        record("queue_age", True, {"oldest_seconds": 0, "queued": 0})
    else:
        record("queue_age", age <= max_queue_age,
               {"oldest_seconds": age, "threshold_seconds": max_queue_age},
               f"oldest queued job is {age}s old (limit {max_queue_age}s)")

# last sync progress ----------------------------------------------------------
raw = os.environ["SYNC_ROW"]
if not raw:
    record("last_sync", False, {"error": "database unreachable"},
           "last-sync check unavailable (database unreachable)")
else:
    age_raw, _, stamp = raw.partition("|")
    age = int(age_raw)
    detail = {"last_progress_at": stamp or None,
              "age_seconds": None if age < 0 else age}
    if age < 0:
        warnings.append("no sync has ever recorded progress")
        record("last_sync", True, detail)
    elif max_sync_age is not None and age > int(max_sync_age):
        record("last_sync", False, {**detail, "threshold_seconds": int(max_sync_age)},
               f"last sync progress is {age}s old (limit {max_sync_age}s)")
    else:
        record("last_sync", True, detail)

# disk ------------------------------------------------------------------------
def parse_df(raw: str) -> dict | None:
    if not raw:
        return None
    avail_kb, used_pct, mount = raw.split("|", 2)
    free_pct = 100 - int(used_pct.rstrip("%"))
    return {"mount": mount, "available_kb": int(avail_kb), "free_pct": free_pct}

var_disk = parse_df(os.environ["VAR_DF"])
if var_disk is None:
    record("disk_var", False, {"error": "df failed"}, "disk check for var/ failed")
else:
    record("disk_var", var_disk["free_pct"] >= min_free_pct,
           {**var_disk, "threshold_free_pct": min_free_pct},
           f"var/ volume has {var_disk['free_pct']}% free (minimum {min_free_pct}%)")

docker_root = os.environ["DOCKER_ROOT"]
docker_disk = parse_df(os.environ["DOCKER_DF"])
if docker_disk is not None:
    record("disk_docker", docker_disk["free_pct"] >= min_free_pct,
           {**docker_disk, "root": docker_root, "threshold_free_pct": min_free_pct},
           f"docker data root has {docker_disk['free_pct']}% free (minimum {min_free_pct}%)")
else:
    detail = {"root": docker_root or None,
              "note": "docker data root not visible on this host (VM-backed engine)"}
    checks["disk_docker"] = {"ok": True, **detail}
    if not docker_root:
        warnings.append("docker engine unreachable; docker disk usage not checked")

# newest backup ---------------------------------------------------------------
newest = os.environ["NEWEST_BACKUP"]
if not newest:
    record("backup_age", False,
           {"newest": None, "backup_root": os.environ["BACKUP_ROOT"]},
           "no sealed backup found in " + os.environ["BACKUP_ROOT"])
else:
    stamp = Path(newest).name
    created = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    age_hours = (datetime.now(UTC) - created).total_seconds() / 3600
    record("backup_age", age_hours <= max_backup_age_hours,
           {"newest": newest, "age_hours": round(age_hours, 2),
            "threshold_hours": max_backup_age_hours},
           f"newest backup is {age_hours:.1f}h old (limit {max_backup_age_hours}h)")

# API -------------------------------------------------------------------------
api_state = os.environ["API_STATE"]
detail = {"state": api_state, "url": os.environ["API_URL"]}
if api_state == "healthy":
    record("api", True, detail)
elif api_state == "not_running":
    warnings.append("API is not running on the loopback port; health not checked")
    checks["api"] = {"ok": True, **detail}
else:
    record("api", False, detail, "API health endpoint returned a non-200 response")

report = {
    "schema_version": "kip.ops-report.v1",
    "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "ok": not failures,
    "checks": checks,
    "failures": failures,
    "warnings": warnings,
}
Path(os.environ["REPORT_JSON"]).write_text(
    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

if os.environ["OUTPUT_MODE"] == "json":
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
else:
    for name in sorted(checks):
        payload = checks[name]
        state = "ok" if payload["ok"] else "FAIL"
        extra = {k: v for k, v in payload.items() if k != "ok"}
        print(f"[{state}] {name}: {json.dumps(extra, ensure_ascii=False, sort_keys=True)}")
    for warning in warnings:
        print(f"[warn] {warning}")
    if failures:
        print("OPS-REPORT FAIL: " + "; ".join(failures))
    else:
        print("OPS-REPORT OK: all checks passed")
sys.exit(1 if failures else 0)
PY
STATUS=$?
set -e

if (( STATUS != 0 )) && [[ -n "${KIP_OPS_WEBHOOK:-}" && -s "$REPORT_JSON" ]]; then
  curl -sS --max-time 10 -X POST -H 'Content-Type: application/json' \
    --data-binary @"$REPORT_JSON" "$KIP_OPS_WEBHOOK" >/dev/null \
    || printf 'ops-report: webhook delivery failed\n' >&2
fi
exit "$STATUS"
