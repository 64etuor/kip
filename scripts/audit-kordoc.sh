#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf 'Kordoc audit unavailable: Node.js and npm are required; no checks were skipped.\n' >&2
  exit 1
fi

cd "$PROJECT_ROOT/requirements/kordoc"
# npm audit reads the lock without checking that its root and overrides still
# describe our manifest. Reject drift before querying the advisory service.
node <<'NODE'
const fs = require('node:fs');
const { isDeepStrictEqual } = require('node:util');
try {
  const manifest = JSON.parse(fs.readFileSync('package.json', 'utf8'));
  const lock = JSON.parse(fs.readFileSync('package-lock.json', 'utf8'));
  const root = lock.packages?.[''];
  if (lock.lockfileVersion !== 3 || !root) throw new Error('unsupported or missing lock root');
  for (const key of ['name', 'version', 'dependencies', 'optionalDependencies', 'devDependencies', 'engines']) {
    if (!isDeepStrictEqual(manifest[key], root[key])) throw new Error(`lock root ${key} differs from package.json`);
  }
  for (const [name, version] of Object.entries(manifest.dependencies)) {
    if (!/^\d+\.\d+\.\d+$/.test(version) || lock.packages[`node_modules/${name}`]?.version !== version) {
      throw new Error(`lock dependency ${name} does not match its exact pin`);
    }
  }
  for (const [name, version] of Object.entries(manifest.overrides)) {
    const entries = Object.entries(lock.packages).filter(([path]) => path.endsWith(`node_modules/${name}`));
    if (!entries.length || entries.some(([, pkg]) => pkg.version !== version)) {
      throw new Error(`lock override ${name} does not match package.json`);
    }
  }
} catch (error) {
  console.error(`Kordoc lock validation failed: ${error.message}`);
  process.exit(1);
}
NODE

# A nonzero registry/network exit is a failed gate, just like high/critical
# findings. Moderate findings remain visible; audit never installs or fixes.
exec npm audit --package-lock-only --omit=dev --audit-level=high
