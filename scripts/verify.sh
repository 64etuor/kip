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
"$PY" -m pytest
if command -v ruff >/dev/null 2>&1; then
  ruff check src tests scripts
fi
if command -v mypy >/dev/null 2>&1; then
  mypy src/kip
fi
# Retrieval regression floor on the reviewed golden set. Self-skips when no
# durable corpus is configured, so it only gates where the corpus exists.
"$PY" scripts/golden_gate.py
printf 'Verification passed.\n'
