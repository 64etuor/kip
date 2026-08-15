#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
PY="$(python_cmd)"
cd "$PROJECT_ROOT"
"$PY" -m compileall -q src tests scripts sdk
while IFS= read -r script; do
  bash -n "$script"
done < <(find scripts examples -type f -name '*.sh' -print | sort)
"$PY" scripts/generate_contracts.py --check
"$PY" scripts/verify_project.py
# Prefer `uv run pytest`: it matches the import/PYTHONPATH semantics CI uses,
# which have previously caught bugs that `python -m pytest` alone missed.
if command -v uv >/dev/null 2>&1; then
  uv run pytest
else
  "$PY" -m pytest
fi
if command -v ruff >/dev/null 2>&1; then
  ruff check src tests scripts
else
  printf 'WARNING: ruff not found — lint skipped; CI will enforce it\n' >&2
fi
if command -v mypy >/dev/null 2>&1; then
  mypy src/kip
else
  printf 'WARNING: mypy not found — type check skipped; CI will enforce it\n' >&2
fi
"$PY" scripts/portable_golden_gate.py
"$PY" scripts/golden_gate.py
printf 'Verification passed.\n'
