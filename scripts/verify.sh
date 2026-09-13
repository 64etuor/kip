#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
PY="$(python_cmd)"
cd "$PROJECT_ROOT"
# Resolve the entire gate before running any checks. Missing tooling is a
# failed verification, including on pip-bootstrapped hosts without uv.
if command -v uv >/dev/null 2>&1; then
  CHECK_RUNNER=(uv run --frozen python -m)
else
  CHECK_RUNNER=("$PY" -m)
fi
for module in pytest ruff mypy pip_audit; do
  if ! "${CHECK_RUNNER[@]}" "$module" --version >/dev/null 2>&1; then
    printf 'Verification unavailable: %s. Run ./scripts/bootstrap.sh and retry; no checks were skipped.\n' "$module" >&2
    exit 1
  fi
done
"$SCRIPT_DIR/audit-kordoc.sh"
"$PY" -m compileall -q src tests scripts sdk
while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts examples -type f -name '*.sh' -print | sort)
"$PY" scripts/generate_contracts.py --check
"$PY" scripts/verify_project.py
# The PostgreSQL integration and contract tests use only the database named by
# KIP_TEST_POSTGRES_URL, never the deployment's own. CI's quality job exports
# one. A local run without it would skip every one of those tests and still
# print "Verification passed", which hides a missing check, so start a
# throwaway container for this run instead. Without Docker this fails loudly.
if [[ -z "${KIP_TEST_POSTGRES_URL:-}" && "${GITHUB_ACTIONS:-}" != "true" ]]; then
  # e2e-common.sh reads these two from its caller: the checkout it finds
  # compose.yaml in (for the pinned image) and the interpreter it picks a free
  # port with. Everything else it needs is set when the file loads.
  E2E_PROJECT_ROOT="$PROJECT_ROOT"
  E2E_PYTHON="$PY"
  # shellcheck source=scripts/e2e-common.sh
  source "$SCRIPT_DIR/e2e-common.sh"
  trap e2e_stop_postgres EXIT
  e2e_start_postgres
  export KIP_TEST_POSTGRES_URL="$E2E_DATABASE_URL"
fi
# Retain the pytest entry point used by CI when uv is available.
if command -v uv >/dev/null 2>&1; then
  uv run --frozen pytest
else
  "$PY" -m pytest
fi
"${CHECK_RUNNER[@]}" ruff check src tests scripts
"${CHECK_RUNNER[@]}" mypy src/kip
"${CHECK_RUNNER[@]}" pip_audit --requirement requirements/runtime.txt --no-deps --disable-pip
"$PROJECT_ROOT/scripts/audit-semantic.sh"
"$PROJECT_ROOT/scripts/golden-gate.sh"
printf 'Verification passed.\n'
