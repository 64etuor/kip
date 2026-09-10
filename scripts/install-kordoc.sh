#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
source "$SCRIPT_DIR/kordoc-runtime.sh"
manifest_dir="$PROJECT_ROOT/requirements/kordoc"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf 'error: Kordoc OCR requires Node.js 20.9+ and npm; install Node.js, then rerun ./scripts/bootstrap.sh\n' >&2
  exit 1
fi

if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)'; then
  printf 'error: Kordoc OCR requires Node.js 20.9+; found %s\n' "$(node --version)" >&2
  exit 1
fi

KORDOC_VERSION="$(kordoc_expected_version)"
readonly KORDOC_VERSION
# The launcher resolves the same default root, so a version or revision bump
# in the manifest moves installation and runtime together.
install_root="${KIP_KORDOC_INSTALL_ROOT:-$(kordoc_runtime_root)}"
package_dir="$install_root/node_modules/kordoc"
model_cache="${KORDOC_MODEL_CACHE:-$PROJECT_ROOT/var/kordoc-models}"

"$SCRIPT_DIR/audit-kordoc.sh"
mkdir -p "$install_root"
cp "$manifest_dir/package.json" "$manifest_dir/package-lock.json" "$install_root/"
(
  cd "$install_root"
  # CPU binaries are bundled. Do not run ONNX's additional-binary extraction
  # hook (adm-zip has an unresolved destination-symlink advisory).
  npm ci --omit=dev --ignore-scripts --no-audit
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
