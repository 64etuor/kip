#!/usr/bin/env bash
# In-place upgrade of a kit-based KIP deployment.
#
#   ./scripts/upgrade.sh --archive kip-starter-kit-X.Y.Z.zip [--dry-run] [--no-bootstrap] [--check|--install-docker|--without-docker]
#   ./scripts/upgrade.sh --latest | --version X.Y.Z      (downloads through scripts/install.sh)
#   ./scripts/upgrade.sh --rollback [UPGRADE_ID]
#
# Kit-owned files are replaced from the archive's manifest; deployment-owned
# paths (.env, config/kip*.toml, .mcp.json, var/, secrets/, ontology
# additions) are never touched. After applying, bootstrap resyncs the locked
# environment and migrate applies append-only migrations.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/runtime-path.sh"
cd "$PROJECT_ROOT"

python_for_upgrade() {
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then command -v python3
  else printf 'upgrade.sh: python3 is required (run ./scripts/bootstrap.sh first)\n' >&2; exit 69; fi
}

archive=""; dry_run=0; bootstrap=1; rollback=""; do_rollback=0; version=""; latest=0
bootstrap_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) [[ $# -ge 2 ]] || { printf 'upgrade.sh: --archive needs a value\n' >&2; exit 2; }; archive="$2"; shift 2 ;;
    --archive=*) archive="${1#--archive=}"; shift ;;
    --version) [[ $# -ge 2 ]] || { printf 'upgrade.sh: --version needs a value\n' >&2; exit 2; }; version="$2"; shift 2 ;;
    --version=*) version="${1#--version=}"; shift ;;
    --latest) latest=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --no-bootstrap) bootstrap=0; shift ;;
    --check|--install-docker|--without-docker) bootstrap_args+=("$1"); shift ;;
    --rollback) do_rollback=1; if [[ $# -ge 2 && "$2" != -* ]]; then rollback="$2"; shift; fi; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) printf 'upgrade.sh: unknown option %s\n' "$1" >&2; exit 2 ;;
  esac
done

PY="$(python_for_upgrade)"
if [[ "$do_rollback" == 1 ]]; then
  if [[ -n "$rollback" ]]; then exec "$PY" "$SCRIPT_DIR/upgrade_kit.py" --deployment "$PROJECT_ROOT" --rollback "$rollback"
  else exec "$PY" "$SCRIPT_DIR/upgrade_kit.py" --deployment "$PROJECT_ROOT" --rollback; fi
fi
if [[ -z "$archive" ]]; then
  if [[ "$latest" == 1 || -n "$version" ]]; then
    install_args=("$PROJECT_ROOT")
    [[ -n "$version" ]] && install_args+=(--version "$version")
    [[ "$bootstrap" == 1 ]] || install_args+=(--no-bootstrap)
    [[ "$dry_run" == 0 ]] || install_args+=(--dry-run)
    exec "$SCRIPT_DIR/install.sh" "${install_args[@]}" ${bootstrap_args[@]+"${bootstrap_args[@]}"}
  fi
  printf 'upgrade.sh: pass --archive ZIP, --latest, --version X.Y.Z or --rollback\n' >&2; exit 2
fi
[[ -f "$archive" ]] || { printf 'upgrade.sh: archive not found: %s\n' "$archive" >&2; exit 2; }

upgrade_args=(--deployment "$PROJECT_ROOT" --archive "$archive")
[[ "$dry_run" == 1 ]] && upgrade_args+=(--dry-run)
"$PY" "$SCRIPT_DIR/upgrade_kit.py" "${upgrade_args[@]}"
if [[ "$dry_run" == 1 || "$bootstrap" == 0 ]]; then exit 0; fi

# The tree now holds the new kit; finish with its own wrappers.
"$PROJECT_ROOT/scripts/bootstrap.sh" ${bootstrap_args[@]+"${bootstrap_args[@]}"}
for arg in ${bootstrap_args[@]+"${bootstrap_args[@]}"}; do [[ "$arg" == "--check" ]] && exit 0; done
if ! "$PROJECT_ROOT/scripts/migrate.sh"; then
  printf 'Action required: migrations were not applied. Start the database (./scripts/app-up.sh --database-only) and run ./scripts/migrate.sh, then ./scripts/kip doctor.\n' >&2
  exit 75
fi
if "$PROJECT_ROOT/scripts/kip" doctor >/dev/null 2>&1; then
  printf 'Upgrade complete. Run ./scripts/kip doctor for the full readiness report.\n'
else
  printf 'Upgrade applied and migrated; ./scripts/kip doctor reported issues, run it for details.\n' >&2
fi
