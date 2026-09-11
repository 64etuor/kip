#!/usr/bin/env bash
# One-command KIP installer and updater.
#
#   curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash
#   curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash -s -- ~/kip --version 3.9.0
#
# Standalone: no Python, uv or project code is required before it runs. It
# downloads the versioned package and its .sha256 sidecar, verifies the
# digest before extracting anything, then either installs into an empty
# directory and runs ./scripts/bootstrap.sh, or - when the directory already
# holds a package-based deployment - hands the verified archive to
# ./scripts/upgrade.sh, which replaces only package-owned files.
set -euo pipefail

REPOSITORY="${KIP_REPOSITORY:-64etuor/kip}"
RELEASE_BASE_URL="${KIP_RELEASE_BASE_URL:-https://github.com/${REPOSITORY}/releases/download}"
LATEST_URL="${KIP_LATEST_URL:-https://api.github.com/repos/${REPOSITORY}/releases/latest}"

usage() {
  cat <<'USAGE'
Usage: install.sh [TARGET_DIR] [--version X.Y.Z] [--check] [--install-docker | --without-docker]
                  [--no-bootstrap] [--dry-run] [--keep-archive]
                  [--no-shell-profile] [--bin-dir DIR] [--keep-launcher]

  TARGET_DIR        Installation directory (default: $KIP_HOME or ~/kip).
                    An existing package-based deployment there is upgraded in place.
  --version X.Y.Z   Install this release instead of the latest one ($KIP_VERSION).
  --check           Only run the read-only prerequisite check after extracting
                    (on an existing deployment the package is applied first).
  --install-docker  Allow bootstrap to install system Docker (explicit consent).
  --without-docker  External-database CLI/MCP installation without Docker.
  --no-bootstrap    Extract and verify only; do not run ./scripts/bootstrap.sh.
  --dry-run         Upgrade only: print the plan and changelog without changing files.
  --keep-archive    Keep the downloaded archive next to TARGET_DIR.
  --no-shell-profile  Do not add the KIP block to your shell profile (PATH/KIP_HOME).
  --bin-dir DIR     Where the global `kip` launcher is written (default: ~/.local/bin).
  --keep-launcher   Upgrade only (used by `kip update`): refresh the launcher only when
                    it already opens TARGET_DIR and leave the shell profile unchanged.

Environment: KIP_VERSION, KIP_HOME, KIP_BIN_DIR, KIP_RELEASE_BASE_URL, KIP_LATEST_URL.
After installation `kip --help`, `kip doctor` and `kip update` work from any directory.
USAGE
}

fail() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

target=""
version="${KIP_VERSION:-}"
bootstrap=1
keep_archive=0
dry_run=0
shell_profile=1
keep_launcher=0
bin_dir="${KIP_BIN_DIR:-$HOME/.local/bin}"
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
    --no-shell-profile) shell_profile=0; shift ;;
    --keep-launcher) keep_launcher=1; shift ;;
    --bin-dir) [[ $# -ge 2 ]] || fail "--bin-dir needs a value"; bin_dir="$2"; shift 2 ;;
    --bin-dir=*) bin_dir="${1#--bin-dir=}"; shift ;;
    -*) usage >&2; fail "unknown option: $1" ;;
    *) [[ -z "$target" ]] || fail "only one TARGET_DIR is accepted"; target="$1"; shift ;;
  esac
done
target="${target:-${KIP_HOME:-$HOME/kip}}"
# One spelling per deployment: absolute, without trailing slashes, and the
# logical path an existing directory reports (what its scripts compute), so
# the launcher it records can be recognised on the next update.
[[ "$target$bin_dir" != *$'\n'* ]] || fail "installation paths must not contain newlines"
[[ "$target" == /* ]] || target="$PWD/$target"
while [[ "$target" == */ && "$target" != / ]]; do target="${target%/}"; done
if [[ -d "$target" ]]; then target="$(cd "$target" && pwd)"; fi
# A relative --bin-dir would put a relative directory on PATH.
[[ "$bin_dir" == /* ]] || bin_dir="$PWD/$bin_dir"
if [[ "$keep_launcher" == 1 ]] && ! [[ -f "$target/VERSION" && ( -f "$target/KIP-MANIFEST.json" || -f "$target/STARTER-KIT-MANIFEST.json" ) ]]; then
  fail "--keep-launcher applies to upgrades; $target is not a KIP deployment (use --bin-dir/--no-shell-profile to control the launcher of a new installation)"
fi

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

extract_upgrader() {
  # Print the path of scripts/upgrade_package.py extracted from archive $1
  # into directory $2, or nothing when the archive predates it.
  local archive="$1" out="$2/upgrade_package.py" member listing=""
  # BusyBox unzip has no -Z; then the python3 branch does the listing.
  if command -v unzip >/dev/null 2>&1 && listing="$(unzip -Z1 "$archive" 2>/dev/null)"; then
    member="$(printf '%s\n' "$listing" | grep -E '^[^/]+/scripts/upgrade_package\.py$' | head -n 1 || true)"
    [[ -n "$member" ]] || return 0
    unzip -p "$archive" "$member" > "$out"
  else
    python3 - "$archive" "$out" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zipped:
    names = [n for n in zipped.namelist() if n.count("/") == 2 and n.endswith("/scripts/upgrade_package.py")]
    if names:
        with open(sys.argv[2], "wb") as handle:
            handle.write(zipped.read(names[0]))
PY
    [[ -f "$out" ]] || return 0
  fi
  printf '%s\n' "$out"
}

launcher_abort() {
  # Fresh installs stop on launcher/profile problems; after a committed
  # upgrade they are reported as warnings so the exit code keeps meaning
  # "was the upgrade applied".
  if [[ "${launcher_strict:-1}" == 1 ]]; then fail "$1"; fi
  printf 'install.sh: warning: %s; the shell profile was not updated\n' "$1" >&2
  launcher_hint="Rerun the installer once the profile problem is fixed, or use $target/scripts/kip"
  return 1
}

shell_quote() {
  # Single-quote for POSIX sh/bash/zsh; embedded ' becomes '\'' .
  local value="$1" quote="'" escaped="'\\''"
  value="${value//$quote/$escaped}"
  printf "'%s'" "$value"
}

launcher_opens_target() {
  [[ -f "$bin_dir/kip" ]] \
    && grep -q 'KIP launcher written by install.sh' "$bin_dir/kip" 2>/dev/null \
    && grep -qxF "KIP_DEPLOYMENT=$(shell_quote "$target")" "$bin_dir/kip" 2>/dev/null
}

refresh_launcher() {
  # `kip update` (--keep-launcher) keeps the launcher current only when it
  # already opens this deployment. Another deployment's launcher, a custom
  # --bin-dir and the shell profile stay exactly as the operator left them.
  if [[ "$keep_launcher" == 1 ]]; then
    if launcher_opens_target; then
      shell_profile=0 install_launcher
      launcher_hint=""
    else
      launcher_hint="The kip launcher and shell profile were left unchanged; use $target/scripts/kip, or rerun the installer on $target (with the same --bin-dir/--no-shell-profile options you installed with) to point kip at it."
    fi
  else
    install_launcher
  fi
}

install_launcher() {
  # A tiny launcher so `kip` works from any directory. Paths are single-quoted
  # (POSIX quoting, unlike %q which emits $'..' for non-ASCII names that sh
  # cannot read) so a directory name can never become shell code in the
  # launcher or the profile. The launcher records the deployment; KIP_HOME
  # overrides it.
  if [[ "$target$bin_dir" == *$'\n'* ]]; then launcher_abort "installation paths must not contain newlines" || return 0; fi
  local quoted_target quoted_bin previous
  quoted_target="$(shell_quote "$target")"
  quoted_bin="$(shell_quote "$bin_dir")"
  mkdir -p "$bin_dir"
  if [[ -e "$bin_dir/kip" ]] && ! grep -q 'KIP launcher written by install.sh' "$bin_dir/kip" 2>/dev/null; then
    cp -p "$bin_dir/kip" "$bin_dir/kip.bak"
    printf 'install.sh: an existing %s/kip was backed up to kip.bak\n' "$bin_dir" >&2
  elif [[ -e "$bin_dir/kip" ]] && ! launcher_opens_target; then
    previous="$(sed -n 's/^# KIP launcher written by install.sh. Deployment: //p' "$bin_dir/kip" | head -n 1)"
    printf 'install.sh: %s/kip opened another deployment (%s); it now opens %s\n' \
      "$bin_dir" "${previous:-unknown}" "$target" >&2
  fi
  printf '#!/usr/bin/env bash\n# KIP launcher written by install.sh. Deployment: %s\nKIP_DEPLOYMENT=%s\nexec "${KIP_HOME:-$KIP_DEPLOYMENT}/scripts/kip" "$@"\n' \
    "$target" "$quoted_target" > "$bin_dir/kip.tmp"
  chmod 755 "$bin_dir/kip.tmp"
  mv -f "$bin_dir/kip.tmp" "$bin_dir/kip"
  if [[ "$shell_profile" == 1 ]]; then
    local profile zdotdir
    case "$(basename "${SHELL:-/bin/sh}")" in
      zsh)
        # ZDOTDIR is usually set in ~/.zshenv without export; ask zsh itself.
        zdotdir="$(zsh -c 'printf %s "${ZDOTDIR:-}"' 2>/dev/null || true)"
        profile="${zdotdir:-${ZDOTDIR:-$HOME}}/.zshrc" ;;
      bash) if [[ "$(uname -s)" == Darwin ]]; then profile="$HOME/.bash_profile"; else profile="$HOME/.bashrc"; fi ;;
      *) profile="$HOME/.profile" ;;
    esac
    # Edit the file the symlink points at, keep its mode, and never create a
    # ~/.bash_profile that would shadow an existing ~/.profile.
    local link_dir depth=0
    while [[ -L "$profile" ]]; do
      (( depth++ < 8 )) || { launcher_abort "$profile is a symlink chain deeper than 8 levels; pass --no-shell-profile" || return 0; }
      link_dir="$(dirname "$profile")"
      profile="$(readlink "$profile")"
      [[ "$profile" == /* ]] || profile="$link_dir/$profile"
    done
    [[ -d "$(dirname "$profile")" ]] || { launcher_abort "cannot write the shell profile $profile: its directory does not exist (use --no-shell-profile to skip)" || return 0; }
    local block
    block="$(printf '# >>> KIP >>>\nexport KIP_HOME=%s\nKIP_BIN=%s\ncase ":$PATH:" in *":$KIP_BIN:"*) ;; *) export PATH="$KIP_BIN:$PATH" ;; esac\nunset KIP_BIN\n# <<< KIP <<<' "$quoted_target" "$quoted_bin")"
    if [[ -f "$profile" ]]; then
      if grep -q '^# >>> KIP >>>$' "$profile" && ! grep -q '^# <<< KIP <<<$' "$profile"; then
        launcher_abort "$profile has an unterminated KIP block; repair it (or remove the '# >>> KIP >>>' line) and rerun" || return 0
      fi
      awk 'BEGIN{skip=0} /^# >>> KIP >>>$/{skip=1; next} /^# <<< KIP <<<$/{skip=0; next} skip==0{print}' "$profile" > "$profile.kip.tmp"
      local mode=""
      mode="$(stat -f '%OLp' "$profile" 2>/dev/null)" || mode="$(stat -c '%a' "$profile" 2>/dev/null)" || mode=""
      [[ -z "$mode" ]] || chmod "$mode" "$profile.kip.tmp"
    else
      : > "$profile.kip.tmp"
      chmod 600 "$profile.kip.tmp"
      if [[ "$profile" == "$HOME/.bash_profile" && -f "$HOME/.profile" ]]; then
        printf '[ -f "$HOME/.profile" ] && . "$HOME/.profile"\n' >> "$profile.kip.tmp"
      fi
    fi
    printf '%s\n' "$block" >> "$profile.kip.tmp"
    mv -f "$profile.kip.tmp" "$profile"
    launcher_hint="Restart your shell (or run: source $profile), then: kip --help"
  else
    launcher_hint="Add $bin_dir to PATH and export KIP_HOME=$target to use kip from any directory"
  fi
}

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

command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
  || fail "curl or wget is required (Ubuntu/Debian: sudo apt-get install curl ca-certificates)"
resolve_version
archive_name="kip-${version}.zip"
printf 'Downloading KIP %s…\n' "$version" >&2
if ! fetch "${RELEASE_BASE_URL}/v${version}/${archive_name}" "$work/$archive_name" 2>"$work/fetch.err"; then
  # Releases before 3.10.0 published the archive under its former name.
  archive_name="kip-starter-kit-${version}.zip"
  if ! fetch "${RELEASE_BASE_URL}/v${version}/${archive_name}" "$work/$archive_name" 2>>"$work/fetch.err"; then
    cat "$work/fetch.err" >&2
    fail "could not download kip-${version}.zip (or its pre-3.10 name) for release v${version}: check the version and network/proxy access; nothing was extracted"
  fi
fi
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
if [[ -f "$target/VERSION" && ( -f "$target/KIP-MANIFEST.json" || -f "$target/STARTER-KIT-MANIFEST.json" ) ]]; then
  [[ -e "$target/.git" ]] && fail "$target is a git checkout; update it with git pull, not the installer"
  current="$(tr -d '[:space:]' < "$target/VERSION")"
  [[ -x "$target/scripts/bootstrap.sh" ]] || fail "$target has a VERSION file but no scripts/bootstrap.sh; it looks like an interrupted install. Move it aside and rerun"
  if [[ "$current" == "$version" ]]; then
    launcher_hint=""
    [[ "$dry_run" == 1 ]] || refresh_launcher  # keep the launcher and profile block current
    printf 'KIP %s is already installed at %s; nothing to do.%s\n' "$version" "$target" "${launcher_hint:+ $launcher_hint}" >&2
    exit 0
  fi
  [[ -x "$target/scripts/upgrade.sh" ]] || fail "$target predates the in-place upgrader; follow docs/DEPLOYMENT_GUIDE.md section 11 once, then use this installer"
  printf 'Upgrading %s -> %s in %s…\n' "$current" "$version" "$target" >&2
  # Apply with the upgrader shipped inside the (already digest-verified)
  # archive: it always understands that archive's manifest, while the
  # deployment's copy may predate it (3.9.x expected STARTER-KIT-MANIFEST.json).
  # Older archives without it fall back to the deployment's own upgrade.sh.
  applied=0
  upgrader="$(extract_upgrader "$work/$archive_name" "$work")"
  if [[ -n "$upgrader" ]]; then
    py="$target/.venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3 || true)"
    [[ -n "$py" ]] || fail "python3 is required to upgrade $target (run its scripts/bootstrap.sh first)"
    apply_args=(--deployment "$target" --archive "$work/$archive_name")
    [[ "$dry_run" == 0 ]] || apply_args+=(--dry-run)
    if "$py" "$upgrader" "${apply_args[@]}"; then status=0; else status=$?; fi
    [[ "$status" != 0 || "$dry_run" == 1 ]] || applied=1
    if [[ "$applied" == 1 && "$bootstrap" == 1 ]]; then
      # The tree now holds the new package; finish with its own wrapper.
      if "$target/scripts/upgrade.sh" --finish ${bootstrap_args[@]+"${bootstrap_args[@]}"}; then status=0; else status=$?; fi
    fi
  else
    upgrade_args=(--archive "$work/$archive_name")
    [[ "$bootstrap" == 1 ]] || upgrade_args+=(--no-bootstrap)
    [[ "$dry_run" == 0 ]] || upgrade_args+=(--dry-run)
    for arg in ${bootstrap_args[@]+"${bootstrap_args[@]}"}; do upgrade_args+=("$arg"); done
    # Not exec: the EXIT trap must still remove the downloaded archive.
    if "$target/scripts/upgrade.sh" "${upgrade_args[@]}"; then status=0; else status=$?; fi
    [[ "$status" != 0 || "$dry_run" == 1 ]] || applied=1
  fi
  if [[ "$applied" == 1 ]]; then
    # Files are committed (even when migrate deferred with exit 75): keep the
    # launcher current, and never turn a committed upgrade into exit 1 here.
    launcher_strict=0 refresh_launcher
    [[ -z "$launcher_hint" ]] || printf '%s\n' "$launcher_hint" >&2
  fi
  exit "$status"
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
  if [[ -n "$(find "$work/extract" -type l -print | head -n 1)" ]]; then
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
[[ ${#roots[@]} -eq 1 && -f "${roots[0]}VERSION" && -x "${roots[0]}scripts/bootstrap.sh" ]] || fail "archive layout was not a single package directory"
# Only remove on failure what this run created; a prepared empty directory
# belongs to the user.
[[ -d "$target" ]] || created_target="$target"
mkdir -p "$target"
target="$(cd "$target" && pwd)"  # resolve ./ and ../ the way the deployment's scripts will
cp -R "${roots[0]}". "$target/"
created_target=""
printf 'Installed KIP %s into %s.\n' "$version" "$target" >&2

install_launcher
cd "$target"
if [[ "$bootstrap" == 0 ]]; then
  printf 'Next: cd %s && ./scripts/bootstrap.sh\n%s\n' "$target" "$launcher_hint"
  exit 0
fi
./scripts/bootstrap.sh ${bootstrap_args[@]+"${bootstrap_args[@]}"}
for arg in ${bootstrap_args[@]+"${bootstrap_args[@]}"}; do [[ "$arg" == "--check" ]] && exit 0; done
# Full manifest verification needs the project environment; run it once it exists.
./scripts/verify-package.sh "$work/$archive_name" >/dev/null
cat <<NEXT
KIP $version is ready in $target.
$launcher_hint
Next:
  kip setup inspect                    # guided deployment (ask your agent: "KIP을 셋업해줘")
  $target/scripts/app-up.sh --database-only   # or: local sample with the bundled database
Update later with: kip update   (or rerun this installer on the same directory)
NEXT
