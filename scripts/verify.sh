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
