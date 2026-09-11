#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/runtime-path.sh"

# Validate switches before any download. The Python stage has the same CLI.
check_only=0
install_docker=0
without_docker=0
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --install-docker) install_docker=1 ;;
    --without-docker) without_docker=1 ;;
    -h|--help)
      printf 'Usage: prerequisites.sh [--check] [--install-docker | --without-docker]\n'
      exit 0 ;;
    *) printf 'Unknown prerequisite option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done
if [[ "$install_docker" == 1 && "$without_docker" == 1 ]]; then
  printf 'Choose --install-docker or --without-docker, not both.\n' >&2; exit 2
fi

compatible_python() {
  if [[ "$1" == /usr/bin/python3 && "$(uname -s)" == Darwin ]] && ! xcode-select -p >/dev/null 2>&1; then return 1; fi
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) and sys.platform in {"darwin","linux"} else 1)' >/dev/null 2>&1
}
selected=""
if [[ -n "${KIP_PYTHON:-}" ]]; then
  selected="$(command -v "$KIP_PYTHON" || true)"
  if [[ -z "$selected" ]] || ! compatible_python "$selected"; then
    printf 'KIP_PYTHON must select Python 3.12+. Correct it before retrying.\n' >&2
    exit 1
  fi
elif [[ -e "$PROJECT_ROOT/.venv" || -L "$PROJECT_ROOT/.venv" ]]; then
  selected="$PROJECT_ROOT/.venv/bin/python"
  if ! compatible_python "$selected"; then
    printf 'Existing .venv is not usable with Python 3.12+. Preserve it under another name, then rerun bootstrap. Nothing was deleted.\n' >&2
    exit 1
  fi
else
  for candidate in python3 python3.13 python3.12; do
    path="$(command -v "$candidate" || true)"
    if [[ -n "$path" ]] && compatible_python "$path"; then selected="$path"; break; fi
  done
fi

if [[ -z "$selected" ]]; then
  if [[ "$check_only" == 1 ]]; then
    printf 'Python 3.12+: missing. Run ./scripts/bootstrap.sh to prepare a project-local runtime.\n' >&2
    exit 1
  fi
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) target=darwin-arm64 ;;
    Darwin/x86_64) target=darwin-x64 ;;
    Linux/aarch64|Linux/arm64) target=linux-arm64 ;;
    Linux/x86_64) target=linux-x64 ;;
    *) printf 'Automatic runtime preparation supports macOS and glibc Linux on arm64/x86_64. Windows: use WSL2.\n' >&2; exit 1 ;;
  esac
  if [[ "$target" == linux-* ]] && ! command -v gzip >/dev/null 2>&1; then
    printf 'gzip is required by GNU tar for the first runtime download. Install gzip and rerun bootstrap.\n' >&2
    exit 1
  fi
  if ! record="$(awk -v target="$target" '$1=="uv" && $2==target {print; n++} END {if(n!=1) exit 1}' "$PROJECT_ROOT/requirements/bootstrap.tsv")"; then
    printf 'Expected exactly one uv asset for this platform in requirements/bootstrap.tsv.\n' >&2; exit 1
  fi
  read -r component target version url checksum <<< "$record"
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || "$url" != https://* || ! "$checksum" =~ ^[a-f0-9]{64}$ ]]; then
    printf 'Invalid version, HTTPS URL or checksum in the uv bootstrap pin.\n' >&2; exit 1
  fi
  runtime="$PROJECT_ROOT/var/runtime"
  if [[ -L "$PROJECT_ROOT/var" || -L "$runtime" || -L "$runtime/bin" ]]; then
    printf 'Review symlinked var/runtime paths before installing project runtimes.\n' >&2; exit 1
  fi
  mkdir -p "$runtime/bin"
  uv_dir="$runtime/uv-$version"
  if [[ -L "$uv_dir" ]]; then printf 'The managed uv directory must not be a symlink.\n' >&2; exit 1; fi
  if [[ ! -x "$uv_dir/uv" || ! -f "$uv_dir/.verified-sha256" ]]; then
    stage="$(mktemp -d "$runtime/.uv-download.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    printf 'Preparing the Python installer in this project…\n' >&2
    if command -v curl >/dev/null 2>&1; then
      curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fLsS --connect-timeout 15 --max-time 300 --max-filesize 67108864 --retry 2 "$url" -o "$stage/archive.tar.gz"
    elif command -v wget >/dev/null 2>&1; then
      wget --https-only --timeout=30 -O "$stage/archive.tar.gz" "$url"
    else
      printf 'curl or wget is required for the first download. On Ubuntu/Debian: sudo apt-get install curl ca-certificates\n' >&2
      exit 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
      actual="$(sha256sum "$stage/archive.tar.gz" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
      actual="$(shasum -a 256 "$stage/archive.tar.gz" | awk '{print $1}')"
    else
      printf 'A SHA-256 checker (sha256sum or shasum) is required; download was not executed.\n' >&2; exit 1
    fi
    [[ "$actual" == "$checksum" ]] || { printf 'uv checksum mismatch; download was not executed.\n' >&2; exit 1; }
    mkdir "$stage/package"
    tar -xzf "$stage/archive.tar.gz" -C "$stage/package" --strip-components=1
    found_version="$("$stage/package/uv" --version)"
    if [[ "$found_version" != "uv $version" && "$found_version" != "uv $version "* ]]; then
      printf 'Downloaded uv reports "%s", expected uv %s; it was not installed.\n' "$found_version" "$version" >&2
      exit 1
    fi
    if [[ -e "$uv_dir" ]]; then printf 'Incomplete runtime directory: %s. Preserve it elsewhere and retry.\n' "$uv_dir" >&2; exit 1; fi
    mv "$stage/package" "$uv_dir"
    printf '%s\n' "$checksum" > "$uv_dir/.verified-sha256"
  fi
  [[ "$(cat "$uv_dir/.verified-sha256")" == "$checksum" ]] || { printf 'uv verification marker differs from the pin.\n' >&2; exit 1; }
  python_version="$(awk '$1=="python" {print $3; exit}' "$PROJECT_ROOT/requirements/bootstrap.tsv")"
  export UV_PYTHON_INSTALL_DIR="$runtime/python"
  "$uv_dir/uv" python install "$python_version" --no-bin --no-config
  selected="$("$uv_dir/uv" python find "$python_version" --system --managed-python --no-config)"
  if ! compatible_python "$selected"; then
    printf 'Managed Python %s at %s failed the compatibility probe (need 3.12+ on macOS/Linux).\n' "$python_version" "$selected" >&2
    exit 1
  fi
  ln -sfn "$selected" "$runtime/bin/python3"
fi
if [[ -n "${stage:-}" ]]; then rm -rf "$stage"; trap - EXIT; fi
exec "$selected" "$SCRIPT_DIR/prerequisites.py" "$@"
