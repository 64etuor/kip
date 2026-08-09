#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
cd "$PROJECT_ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env; change the generated passwords before non-local use." >&2
fi
if [[ ! -f config/kip.toml ]]; then
  cp config/kip.example.toml config/kip.toml
fi
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[postgres,api,identity,extractors,mcp,telemetry,dev]'
mkdir -p var/cas var/backups var/log
python scripts/create_sample_xlsx.py
python scripts/generate_contracts.py
printf 'Bootstrap complete. Next: ./scripts/dev-up.sh && ./scripts/migrate.sh\n'
