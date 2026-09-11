#!/usr/bin/env bash
# Retrieval regression gates, run with the project interpreter and .env.
#
#   ./scripts/golden-gate.sh              portable gate, then the private gate
#   ./scripts/golden-gate.sh --portable   portable synthetic gate only (no corpus needed)
#   ./scripts/golden-gate.sh --private    private reviewed-corpus gate only
#
# The private gate skips (exit 0) when the reviewed corpus is not indexed;
# set KIP_REQUIRE_PRIVATE_GOLDEN=1 to make that a failure.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
PY="$(python_cmd)"

portable=1
private=1
case "${1:-}" in
  "") ;;
  --portable) private=0 ;;
  --private) portable=0 ;;
  -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
  *) printf 'golden-gate.sh: unknown option %s\n' "$1" >&2; exit 2 ;;
esac

if [[ "$portable" == 1 ]]; then "$PY" "$SCRIPT_DIR/portable_golden_gate.py"; fi
if [[ "$private" == 1 ]]; then "$PY" "$SCRIPT_DIR/golden_gate.py"; fi
