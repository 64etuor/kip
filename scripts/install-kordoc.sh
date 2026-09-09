#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly KORDOC_VERSION=4.8.0
readonly KORDOC_RUNTIME_REVISION=1
readonly KORDOC_ADM_ZIP_VERSION=0.6.0
readonly KORDOC_SHARP_VERSION=0.35.3
install_root="${KIP_KORDOC_INSTALL_ROOT:-$PROJECT_ROOT/var/kordoc-$KORDOC_VERSION-r$KORDOC_RUNTIME_REVISION}"
package_dir="$install_root/node_modules/kordoc"
model_cache="${KORDOC_MODEL_CACHE:-$PROJECT_ROOT/var/kordoc-models}"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf 'error: Kordoc OCR requires Node.js 18+ and npm; install Node.js, then rerun ./scripts/bootstrap.sh\n' >&2
  exit 1
fi

if ! node -e 'const [major] = process.versions.node.split(".").map(Number); process.exit(major >= 18 ? 0 : 1)'; then
  printf 'error: Kordoc OCR requires Node.js 18+; found %s\n' "$(node --version)" >&2
  exit 1
fi

mkdir -p "$install_root"
(
  cd "$install_root"
  if [[ ! -f package.json ]]; then
    npm init --yes >/dev/null
  fi
  npm pkg delete dependencies optionalDependencies devDependencies peerDependencies overrides
  npm pkg set --json private=true
  npm pkg set \
    "name=kip-kordoc-runtime" \
    "dependencies.kordoc=$KORDOC_VERSION" \
    "overrides.adm-zip=$KORDOC_ADM_ZIP_VERSION" \
    "overrides.sharp=$KORDOC_SHARP_VERSION"
  npm install --no-package-lock --omit=dev
)

installed_version="$(
  KIP_KORDOC_PACKAGE_DIR="$package_dir" \
    KORDOC_MODEL_CACHE="$model_cache" \
    "$SCRIPT_DIR/kordoc" --version
)"
if [[ "$installed_version" != "$KORDOC_VERSION" ]]; then
  printf 'error: expected Kordoc %s, found %s\n' "$KORDOC_VERSION" "$installed_version" >&2
  exit 1
fi

KIP_KORDOC_PACKAGE_DIR="$package_dir" \
  KORDOC_MODEL_CACHE="$model_cache" \
  KORDOC_OFFLINE=0 \
  "$SCRIPT_DIR/kordoc" check-ocr-models
printf 'Kordoc %s and Korean OCR models are ready.\n' "$KORDOC_VERSION"
