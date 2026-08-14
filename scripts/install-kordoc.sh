#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly KORDOC_VERSION=4.7.3
install_root="${KIP_KORDOC_INSTALL_ROOT:-$PROJECT_ROOT/var/kordoc}"
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

installed_version=""
if [[ -f "$package_dir/dist/cli.js" ]]; then
  installed_version="$(
    KIP_KORDOC_PACKAGE_DIR="$package_dir" \
      KORDOC_MODEL_CACHE="$model_cache" \
      "$SCRIPT_DIR/kordoc" --version 2>/dev/null || true
  )"
fi
if [[ "$installed_version" != "$KORDOC_VERSION" ]]; then
  npm install \
    --prefix "$install_root" \
    --no-save \
    --no-package-lock \
    --omit=dev \
    "kordoc@$KORDOC_VERSION"
fi

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
