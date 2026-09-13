#!/usr/bin/env bash
# End-to-end check of the SHIPPED ARTIFACT, not this source tree.
#
# Builds the package archive the release publishes, serves it from a local
# release mirror, installs it with scripts/install.sh into a clean directory
# exactly as an operator would (launcher and shell profile included, under a
# throwaway HOME), bootstraps it, migrates against PostgreSQL, syncs the
# bundled sample-data, and then asserts on the envelopes that `search`, `read`
# and `xlsx-read` return through the installed `kip`.
#
#   ./scripts/e2e-install.sh                    # starts a throwaway PostgreSQL
#   KIP_E2E_DATABASE_URL=postgresql://... ./scripts/e2e-install.sh
#   ./scripts/e2e-install.sh --keep             # keep the work tree for inspection
#
# Runtime, measured: 1m17s end to end on a developer machine with the uv and
# npm caches already warm. On a GitHub ubuntu-latest runner the locked
# environment, the Kordoc runtime and the Korean OCR models are fetched cold,
# which is the bulk of the cost: expect 6-8 minutes there.
#
# Semantic search is off (KIP_SEMANTIC=off): the model runtime is a 1.2 GB
# download and is covered by scripts/e2e-semantic.sh instead. This check
# therefore asserts a lexical deployment and fails on ANY meta.warnings.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=e2e-common.sh
source "$SCRIPT_DIR/e2e-common.sh"

keep_work=0
archive=""
work=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) keep_work=1; shift ;;
    --archive) [[ $# -ge 2 ]] || e2e_fail "--archive needs a value"; archive="$2"; shift 2 ;;
    --work) [[ $# -ge 2 ]] || e2e_fail "--work needs a value"; work="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) e2e_fail "unknown option: $1" ;;
  esac
done

E2E_PYTHON="${KIP_E2E_PYTHON:-python3}"
e2e_require_command "$E2E_PYTHON"
version="$(tr -d '[:space:]' < "$E2E_PROJECT_ROOT/VERSION")"
checker="$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py"
[[ -f "$checker" ]] || e2e_fail "missing $checker"

if [[ -z "$work" ]]; then work="$(mktemp -d "${TMPDIR:-/tmp}/kip-e2e-install.XXXXXX")"; fi
mkdir -p "$work"
# -P: the physical path. Scripts under test resolve symlinks (backup.sh
# uses `pwd -P`), and on macOS /tmp is a symlink, so a logical path here would
# name a directory that is not the one bind-mounted into a container.
work="$(cd "$work" && pwd -P)"
cleanup() {
  local status=$?
  e2e_stop_postgres
  if [[ "$keep_work" == 1 ]]; then
    printf 'e2e: work tree kept at %s\n' "$work" >&2
  else
    rm -rf "$work"
  fi
  e2e_assert_real_home_unchanged || exit 1
  return "$status"
}
trap cleanup EXIT

# ---------------------------------------------------------------- build
e2e_log "Building the package archive the release publishes"
if [[ -z "$archive" ]]; then
  archive="$work/dist/kip-$version.zip"
  e2e_build_package "$archive"
fi
[[ -f "$archive" ]] || e2e_fail "package archive not found: $archive"
[[ -f "$archive.sha256" ]] || e2e_fail "package archive has no .sha256 sidecar: $archive.sha256"

# ---------------------------------------------------------------- mirror
# install.sh downloads from KIP_RELEASE_BASE_URL/vX.Y.Z/, verifies the
# .sha256 sidecar before extracting anything, and only then unpacks. Serving
# the candidate from a local file:// mirror exercises that whole path
# (curl restricts the installer to https and file, never plain http).
mirror="$work/releases"
mkdir -p "$mirror/v$version"
cp "$archive" "$archive.sha256" "$mirror/v$version/"

# ---------------------------------------------------------------- install
home="$work/home"
mkdir -p "$home"
deployment="$work/deployment"

e2e_log "Installing KIP $version the way an operator does"
# --bin-dir names the installer's own default under the throwaway HOME. It is
# passed anyway: install.sh prefers KIP_BIN_DIR over HOME, and the launcher
# location must never depend on what the environment happens to export.
status=0
e2e_clean_env \
  HOME="$home" \
  SHELL=/bin/bash \
  KIP_RELEASE_BASE_URL="file://$mirror" \
  KIP_SEMANTIC=off \
  bash "$E2E_PROJECT_ROOT/scripts/install.sh" "$deployment" \
    --version "$version" --without-docker --bin-dir "$home/.local/bin" || status=$?
e2e_assert_status 0 "$status" "scripts/install.sh completed"

[[ -f "$deployment/VERSION" ]] || e2e_fail "no VERSION in the installed deployment"
[[ "$(tr -d '[:space:]' < "$deployment/VERSION")" == "$version" ]] \
  || e2e_fail "installed deployment reports $(cat "$deployment/VERSION"), expected $version"

# ------------------------------------------------- launcher and profile
launcher="$home/.local/bin/kip"
[[ -x "$launcher" ]] || e2e_fail "the installer wrote no executable launcher at $launcher"
e2e_assert_contains "$launcher" "KIP launcher written by install.sh" "the launcher carries its marker"
e2e_assert_contains "$launcher" "KIP_DEPLOYMENT='$deployment'" "the launcher opens the deployment it installed"

profile="$home/.bashrc"
[[ "$(uname -s)" != Darwin ]] || profile="$home/.bash_profile"
[[ -f "$profile" ]] || e2e_fail "the installer wrote no shell profile block at $profile"
e2e_assert_contains "$profile" "# >>> KIP >>>" "the shell profile carries the KIP block"
e2e_assert_contains "$profile" "export KIP_HOME='$deployment'" "the shell profile exports KIP_HOME"
e2e_assert_contains "$profile" "# <<< KIP <<<" "the KIP block is terminated"

e2e_assert_real_home_unchanged \
  || e2e_fail "the installer changed this machine's own kip launcher or shell profile"

# ------------------------------------------------------------- database
e2e_resolve_database
e2e_log "Pointing the deployment at PostgreSQL and migrating"
grep -q "^KIP_DATABASE_URL=" "$deployment/.env" \
  || e2e_fail "the installed .env carries no KIP_DATABASE_URL line"
e2e_set_dotenv "$deployment/.env" "KIP_DATABASE_URL=$E2E_DATABASE_URL"

# Every command below runs through the GLOBAL launcher, not through
# $deployment/scripts/kip: an operator's `kip` is the launcher, and a launcher
# that resolves the wrong deployment is the defect this proves absent.
run_kip() {
  e2e_clean_env HOME="$home" SHELL=/bin/bash "$launcher" "$@"
}

status=0
run_kip migrate > "$work/migrate.json" || status=$?
e2e_assert_status 0 "$status" "kip migrate through the installed launcher"

e2e_log "Syncing the bundled sample-data"
status=0
run_kip sync run --source sample > "$work/sync.json" || status=$?
e2e_assert_status 0 "$status" "kip sync run --source sample"
"$E2E_PYTHON" - "$work/sync.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not payload.get("ok"):
    raise SystemExit(f"sync envelope is not ok: {payload.get('error')}")
summary = payload.get("data") or {}
failed = summary.get("failed")
if failed:
    raise SystemExit(f"sync reported failed={failed}; exit code 0 does not prove failed=0")
inserted = summary.get("inserted") or 0
if inserted < 1:
    raise SystemExit(f"sync inserted {inserted} units from the bundled sample-data")
print(f"sync: inserted={inserted} failed={failed}")
PY

# ------------------------------------------------------------- envelopes
# A lexical deployment that just indexed the bundled corpus must warn about
# nothing at all. Any new warning is a behaviour change the release has to
# declare here on purpose.
allow=()

e2e_log "Retrieval through the installed kip"
status=0
run_kip version > "$work/version.json" || status=$?
e2e_assert_status 0 "$status" "kip version"
"$E2E_PYTHON" "$checker" version "$work/version.json" --expect "$version" ${allow[@]+"${allow[@]}"}

status=0
run_kip capabilities > "$work/capabilities.json" || status=$?
e2e_assert_status 0 "$status" "kip capabilities"
"$E2E_PYTHON" "$checker" capabilities "$work/capabilities.json" \
  --repository postgresql --no-expect-semantic ${allow[@]+"${allow[@]}"}

status=0
run_kip search "정산" --limit 5 > "$work/search.json" || status=$?
e2e_assert_status 0 "$status" "kip search"
"$E2E_PYTHON" "$checker" search "$work/search.json" --min-hits 2 \
  --emit-shell "$work/ids.sh" ${allow[@]+"${allow[@]}"}
# shellcheck source=/dev/null
source "$work/ids.sh"
: "${KIP_E2E_UNIT_ID:?search emitted no unit id}" "${KIP_E2E_XLSX_ARTIFACT_ID:?search emitted no workbook artifact id}"

status=0
run_kip read --unit-id "$KIP_E2E_UNIT_ID" > "$work/read.json" || status=$?
e2e_assert_status 0 "$status" "kip read"
"$E2E_PYTHON" "$checker" read "$work/read.json" --unit-id "$KIP_E2E_UNIT_ID" ${allow[@]+"${allow[@]}"}

status=0
run_kip xlsx-read --artifact-id "$KIP_E2E_XLSX_ARTIFACT_ID" \
  --sheet "$KIP_E2E_XLSX_SHEET" --range "$KIP_E2E_XLSX_RANGE" > "$work/xlsx.json" || status=$?
e2e_assert_status 0 "$status" "kip xlsx-read"
# The bundled workbook's own values (scripts/create_sample_xlsx.py): a number
# read through KIP must be the workbook's number, never a snippet's rendering.
"$E2E_PYTHON" "$checker" xlsx "$work/xlsx.json" \
  --expect-cell 'A1=구분' --expect-cell 'C2=1500000' ${allow[@]+"${allow[@]}"}

# ------------------------------------------------------------ exit codes
# docs/TRD.md 29.4 is a public contract: an agent branches on these.
e2e_log "Exit codes an operator and an agent depend on"
status="$(e2e_capture "$work/not-found.json" run_kip read --unit-id unit_definitely_absent)"
e2e_assert_status 4 "$status" "a typed NotFoundError exits 4"
"$E2E_PYTHON" "$checker" error "$work/not-found.json.stderr" --error-code not_found

status="$(e2e_capture "$work/validation.json" run_kip search --mode nonsense "정산")"
e2e_assert_status 3 "$status" "a typed validation error exits 3"
"$E2E_PYTHON" "$checker" error "$work/validation.json.stderr" --error-code validation_error

status="$(e2e_capture "$work/usage.json" run_kip no-such-command)"
e2e_assert_status 2 "$status" "an unknown command exits 2"

e2e_log "PASS: the shipped $version package installs, bootstraps, migrates, syncs and answers"
