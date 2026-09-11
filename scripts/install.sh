#!/usr/bin/env bash
# One-command KIP installer and updater.
#
#   curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash
#   curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash -s -- ~/kip --version 3.9.0
#
# Standalone: no Python, uv or project code is required before it runs. It
# downloads the versioned starter kit and its .sha256 sidecar, verifies the
# digest before extracting anything, then either installs into an empty
# directory and runs ./scripts/bootstrap.sh, or - when the directory already
# holds a kit-based deployment - hands the verified archive to
# ./scripts/upgrade.sh, which replaces only kit-owned files.
set -euo pipefail

REPOSITORY="${KIP_REPOSITORY:-64etuor/kip}"
RELEASE_BASE_URL="${KIP_RELEASE_BASE_URL:-https://github.com/${REPOSITORY}/releases/download}"
LATEST_URL="${KIP_LATEST_URL:-https://api.github.com/repos/${REPOSITORY}/releases/latest}"

usage() {
  cat <<'USAGE'
Usage: install.sh [TARGET_DIR] [--version X.Y.Z] [--check] [--install-docker | --without-docker]
                  [--no-bootstrap] [--dry-run] [--keep-archive]

  TARGET_DIR        Installation directory (default: $KIP_HOME or ~/kip).
                    An existing kit-based deployment there is upgraded in place.
  --version X.Y.Z   Install this release instead of the latest one ($KIP_VERSION).
  --check           Only run the read-only prerequisite check after extracting.
  --install-docker  Allow bootstrap to install system Docker (explicit consent).
  --without-docker  External-database CLI/MCP installation without Docker.
  --no-bootstrap    Extract and verify only; do not run ./scripts/bootstrap.sh.
  --dry-run         Upgrade only: print the plan and changelog without changing files.
  --keep-archive    Keep the downloaded archive next to TARGET_DIR.

Environment: KIP_VERSION, KIP_HOME, KIP_RELEASE_BASE_URL, KIP_LATEST_URL.
USAGE
}

fail() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

target=""
version="${KIP_VERSION:-}"
bootstrap=1
keep_archive=0
dry_run=0
bootstrap_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --version) [[ $# -ge 2 ]] || fail "--version needs a value"; version="$2"; shift 2 ;;
    --version=*) version="${1#--version=}"; shift ;;
    --check|--install-docker|--without-docker) bootstrap_args+=("$1"); shift ;;
    --no-bootstrap) bootstrap=0; shift ;;
    --dry-run) dry_run=1; shift ;;
    --keep-archive) keep_archive=1; shift ;;
    -*) usage >&2; fail "unknown option: $1" ;;
    *) [[ -z "$target" ]] || fail "only one TARGET_DIR is accepted"; target="$1"; shift ;;
  esac
done
target="${target:-${KIP_HOME:-$HOME/kip}}"

fetch() {
  # fetch URL DEST: HTTPS-only (file:// allowed for local mirrors), bounded size.
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https,file' --proto-redir '=https' --tlsv1.2 -fLsS --connect-timeout 15 \
      --max-time 600 --max-filesize 314572800 --retry 2 "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --timeout=30 -qO "$2" "$1"
  else
    fail "curl or wget is required (Ubuntu/Debian: sudo apt-get install curl ca-certificates)"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else fail "sha256sum or shasum is required to verify the download"; fi
}

work="$(mktemp -d)"
created_target=""
cleanup() {
  rm -rf "$work"
  # A fresh install that stopped before "Installed" must not leave a partial
  # tree the installer would later misread as a broken deployment.
  if [[ -n "$created_target" ]]; then rm -rf "$created_target"; fi
}
trap cleanup EXIT

resolve_version() {
  if [[ -n "$version" ]]; then
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "--version must look like X.Y.Z"
    return
  fi
  fetch "$LATEST_URL" "$work/latest.json" || fail "could not resolve the latest release; pass --version X.Y.Z"
  # Take the first literal "tag_name" key (pretty-printed or compact JSON).
  # A quoted tag inside the release body is escaped as \"tag_name\" and
  # therefore never matches the unescaped key.
  version="$(awk 'BEGIN{RS="\"tag_name\""} NR>1 {sub(/^[[:space:]]*:[[:space:]]*"v/, ""); sub(/".*/, ""); print; exit}' "$work/latest.json" | tr -d '\n')"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "latest release tag was not recognised; pass --version X.Y.Z"
}

resolve_version
archive_name="kip-starter-kit-${version}.zip"
printf 'Downloading KIP %s…\n' "$version" >&2
fetch "${RELEASE_BASE_URL}/v${version}/${archive_name}" "$work/$archive_name" \
  || fail "release v${version} has no ${archive_name}; nothing was extracted"
fetch "${RELEASE_BASE_URL}/v${version}/${archive_name}.sha256" "$work/$archive_name.sha256" \
  || fail "release v${version} has no ${archive_name}.sha256 sidecar; nothing was extracted"

expected="$(awk 'NR==1 {print $1}' "$work/$archive_name.sha256")"
[[ "$expected" =~ ^[a-f0-9]{64}$ ]] || fail "the .sha256 sidecar is malformed; nothing was extracted"
[[ "$(awk 'NR==1 {print $2}' "$work/$archive_name.sha256")" == "$archive_name" ]] || fail "the .sha256 sidecar names a different archive; nothing was extracted"
actual="$(sha256_of "$work/$archive_name")"
[[ "$actual" == "$expected" ]] || fail "archive checksum mismatch (expected $expected, got $actual); nothing was extracted"
printf 'Verified %s (sha256 %s).\n' "$archive_name" "$actual" >&2

if [[ "$keep_archive" == 1 ]]; then
  mkdir -p "$(dirname "$target")"
  cp "$work/$archive_name" "$work/$archive_name.sha256" "$(dirname "$target")/"
fi

# Existing deployment: upgrade in place through the deployment's own tooling.
if [[ -f "$target/VERSION" && -f "$target/STARTER-KIT-MANIFEST.json" ]]; then
  [[ -e "$target/.git" ]] && fail "$target is a git checkout; update it with git pull, not the installer"
  current="$(tr -d '[:space:]' < "$target/VERSION")"
  [[ -x "$target/scripts/bootstrap.sh" ]] || fail "$target has a VERSION file but no scripts/bootstrap.sh; it looks like an interrupted install. Move it aside and rerun"
  if [[ "$current" == "$version" ]]; then
    printf 'KIP %s is already installed at %s; nothing to do.\n' "$version" "$target" >&2
    exit 0
  fi
  [[ -x "$target/scripts/upgrade.sh" ]] || fail "$target predates the in-place upgrader; follow docs/STARTER_KIT_GUIDE.md section 11 once, then use this installer"
  printf 'Upgrading %s -> %s in %s…\n' "$current" "$version" "$target" >&2
  upgrade_args=(--archive "$work/$archive_name")
  [[ "$bootstrap" == 1 ]] || upgrade_args+=(--no-bootstrap)
  [[ "$dry_run" == 0 ]] || upgrade_args+=(--dry-run)
  for arg in ${bootstrap_args[@]+"${bootstrap_args[@]}"}; do upgrade_args+=("$arg"); done
  # Not exec: the EXIT trap must still remove the downloaded archive.
  "$target/scripts/upgrade.sh" "${upgrade_args[@]}"
  exit $?
fi

# Fresh installation: the target must be absent or empty.
[[ "$dry_run" == 0 ]] || fail "--dry-run applies to upgrades; $target is not a KIP deployment"
if [[ -e "$target" ]]; then
  [[ -d "$target" ]] || fail "$target exists and is not a directory"
  [[ -z "$(ls -A "$target")" ]] || fail "$target is not empty and is not a KIP deployment; choose another directory"
fi

mkdir -p "$work/extract"
if command -v unzip >/dev/null 2>&1; then
  # -n: never prompt (stdin may be the script itself under curl | bash).
  unzip -qn "$work/$archive_name" -d "$work/extract"
  if find "$work/extract" -type l | grep -q .; then
    fail "archive contains symbolic links; nothing was installed"
  fi
elif command -v python3 >/dev/null 2>&1; then
  python3 - "$work/$archive_name" "$work/extract" <<'PY'
import pathlib, stat, sys, zipfile
archive, dest = sys.argv[1], pathlib.Path(sys.argv[2])
with zipfile.ZipFile(archive) as zipped:
    for info in zipped.infolist():
        name = info.filename
        if name.startswith("/") or ".." in pathlib.PurePosixPath(name).parts:
            raise SystemExit(f"unsafe archive entry: {name}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SystemExit(f"archive contains symbolic links; nothing was installed: {name}")
        zipped.extract(info, dest)
        mode = (info.external_attr >> 16) & 0o777
        if mode and not info.is_dir():
            (dest / name).chmod(mode | stat.S_IRUSR)
PY
else
  fail "unzip or python3 is required to extract the archive"
fi
roots=("$work"/extract/*/)
[[ ${#roots[@]} -eq 1 && -f "${roots[0]}VERSION" && -x "${roots[0]}scripts/bootstrap.sh" ]] || fail "archive layout was not a single kit directory"
# Only remove on failure what this run created; a prepared empty directory
# belongs to the user.
[[ -d "$target" ]] || created_target="$target"
mkdir -p "$target"
cp -R "${roots[0]}". "$target/"
created_target=""
printf 'Installed KIP %s into %s.\n' "$version" "$target" >&2

cd "$target"
if [[ "$bootstrap" == 0 ]]; then
  printf 'Next: cd %s && ./scripts/bootstrap.sh\n' "$target"
  exit 0
fi
./scripts/bootstrap.sh ${bootstrap_args[@]+"${bootstrap_args[@]}"}
for arg in ${bootstrap_args[@]+"${bootstrap_args[@]}"}; do [[ "$arg" == "--check" ]] && exit 0; done
# Full manifest verification needs the project environment; run it once it exists.
./scripts/verify-starter-kit.sh "$work/$archive_name" >/dev/null
cat <<NEXT
KIP $version is ready in $target.
Next:
  cd $target
  ./scripts/kip setup inspect          # guided deployment (ask your agent: "KIP을 셋업해줘")
  ./scripts/app-up.sh --database-only  # or: local sample with the bundled database
Update later with the same command; an existing installation is upgraded in place.
NEXT
