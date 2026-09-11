#!/usr/bin/env bash
# Audit the locked model runtime (requirements/semantic.txt) for known
# vulnerabilities. Reviewed advisories are listed with the reason they do not
# apply; any other finding fails, so a new advisory is never silently accepted.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

reviewed=(
  # click.edit() command injection; the server never calls click.edit().
  PYSEC-2026-2132
  # transformers <5.x advisories: each needs an attacker-controlled model
  # repository, checkpoint, Trainer RNG state or save_pretrained() target.
  # The runtime loads only two commit-pinned revisions, offline once cached,
  # on a loopback-only server, and never trains or saves models.
  PYSEC-2025-217 PYSEC-2026-2288 PYSEC-2026-2289 PYSEC-2026-2290 PYSEC-2026-3929
)
ignore_args=()
for id in "${reviewed[@]}"; do ignore_args+=(--ignore-vuln "$id"); done

if command -v uv >/dev/null 2>&1; then
  runner=(uv run --frozen pip-audit)
else
  runner=("$(python_cmd)" -m pip_audit)
fi
exec "${runner[@]}" --requirement "$PROJECT_ROOT/requirements/semantic.txt" --no-deps --disable-pip "${ignore_args[@]}"
