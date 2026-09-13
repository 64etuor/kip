#!/usr/bin/env bash
# End-to-end check of the UPGRADE path: install the previous published
# release, then move it to the candidate build with `kip update`.
#
# What it proves, in the order the 3.12.1 defect report listed it:
#   * the version moved,
#   * deployment-owned files (.env, config/kip.toml, .mcp.json, var/) survived,
#   * the global `kip` launcher of a SECOND deployment this job also creates
#     was not repointed at the deployment being updated,
#   * no shell profile the job did not ask for was modified, including this
#     machine's own,
#   * agent skills the previous release installed followed the upgrade: its
#     legacy personal copy is adopted (by the one-time
#     `install-agent-files.sh --refresh` when the previous release's own
#     finish step predates the refresh, as 3.15.0's does on `--archive`), its
#     record-less project copy still answers through the kept pointer, and
#     `upgrade.sh --finish` refreshes a registered copy that went stale
#     (ADR-067).
#
#   ./scripts/e2e-upgrade.sh                    # starts a throwaway PostgreSQL
#   KIP_E2E_PREVIOUS_VERSION=3.13.1 ./scripts/e2e-upgrade.sh
#
# The previous release is downloaded from the repository's own published
# releases. There is no offline fallback on purpose: a run that cannot reach
# the release assets FAILS and says so, because an upgrade check that silently
# skips is worse than no upgrade check.
#
# Runtime, measured: 1m19s on a developer machine with warm caches (two
# installs, two bootstraps of the locked environment, two upgrades). On a
# GitHub ubuntu-latest runner both bootstraps download cold: expect 10-14
# minutes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=e2e-common.sh
source "$SCRIPT_DIR/e2e-common.sh"

keep_work=0
archive=""
work=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) keep_work=1; shift ;;
    --archive) [[ $# -ge 2 ]] || e2e_fail "--archive needs a value"; archive="$2"; shift 2 ;;
    --work) [[ $# -ge 2 ]] || e2e_fail "--work needs a value"; work="$2"; shift 2 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) e2e_fail "unknown option: $1" ;;
  esac
done

E2E_PYTHON="${KIP_E2E_PYTHON:-python3}"
e2e_require_command "$E2E_PYTHON"
e2e_require_command curl
repository="${KIP_REPOSITORY:-64etuor/kip}"
release_base="${KIP_RELEASE_BASE_URL:-https://github.com/${repository}/releases/download}"
version="$(tr -d '[:space:]' < "$E2E_PROJECT_ROOT/VERSION")"

if [[ -z "$work" ]]; then work="$(mktemp -d "${TMPDIR:-/tmp}/kip-e2e-upgrade.XXXXXX")"; fi
mkdir -p "$work"
# -P: the physical path. Scripts under test resolve symlinks (backup.sh
# uses `pwd -P`), and on macOS /tmp is a symlink, so a logical path here would
# name a directory that is not the one bind-mounted into a container.
work="$(cd "$work" && pwd -P)"
cleanup() {
  local status=$?
  e2e_stop_postgres
  if [[ "$keep_work" == 1 ]]; then
    printf 'e2e: work tree kept at %s\n' "$work" >&2
  else
    rm -rf "$work"
  fi
  e2e_assert_real_home_unchanged || exit 1
  return "$status"
}
trap cleanup EXIT

# -------------------------------------------------- previous published tag
# The repository's own tags, newest first, excluding the candidate and
# anything newer. A tag whose release was never published (a tag that failed
# the gate) has no assets, so the first tag whose .sha256 sidecar answers is
# the previous published release.
candidate_tags() {
  # `python3 -c`, not `python3 -`: stdin carries the tag list.
  git -C "$E2E_PROJECT_ROOT" tag --list 'v*' | "$E2E_PYTHON" -c '
import sys

current = tuple(int(part) for part in sys.argv[1].split("."))
found = []
for line in sys.stdin:
    tag = line.strip()
    if not tag.startswith("v"):
        continue
    try:
        parsed = tuple(int(part) for part in tag[1:].split("."))
    except ValueError:
        continue
    if len(parsed) == 3 and parsed < current:
        found.append(parsed)
for parsed in sorted(found, reverse=True):
    print(".".join(str(part) for part in parsed))
' "$version"
}

# release_asset_published VERSION: 0 when the sidecar answers 200, 1 when the
# host answers 404 (that tag was never published). Anything else - no route,
# a timeout, a 5xx, a 403 rate limit - is retried with backoff and then FAILS
# THE RUN. Only a 404 may send the check to an older tag: treating a network
# error as "never published" would silently upgrade from the wrong release.
release_asset_published() {
  local url="$release_base/v$1/kip-$1.zip.sha256" attempt=1 attempts=4 delay=5 code
  while :; do
    code="$(curl --proto '=https' --proto-redir '=https' --tlsv1.2 -sSL \
      --connect-timeout 15 --max-time 60 --output /dev/null --write-out '%{http_code}' \
      "$url" 2> "$work/release-probe.err")" || true
    case "$code" in
      200) return 0 ;;
      404) return 1 ;;
    esac
    if (( attempt >= attempts )); then
      e2e_fail "could not determine whether v$1 was published: $url answered HTTP ${code:-000} $attempts times
  ($(tr '\n' ' ' < "$work/release-probe.err")). This is a network or host failure, not a missing
  release, so the check stops instead of falling back to an older tag. Re-run the failed job."
    fi
    e2e_note "probe of $url answered HTTP ${code:-000} (attempt $attempt of $attempts); retrying in ${delay}s"
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

e2e_log "Resolving the previous published release"
unpublished=0
previous="${KIP_E2E_PREVIOUS_VERSION:-}"
if [[ -n "$previous" ]]; then
  e2e_note "using the supplied KIP_E2E_PREVIOUS_VERSION=$previous"
else
  tags="$(candidate_tags)"
  [[ -n "$tags" ]] || e2e_fail "this repository has no tag older than $version to upgrade from"
  while IFS= read -r tag; do
    [[ -n "$tag" ]] || continue
    if release_asset_published "$tag"; then previous="$tag"; break; fi
    unpublished=$((unpublished + 1))
    e2e_note "v$tag answered 404 for kip-$tag.zip.sha256 (never published); trying the tag before it"
  done <<< "$tags"
fi
if [[ -z "$previous" ]]; then
  e2e_fail "all $unpublished of this repository's older tags answered 404 at $release_base:
  no older release was ever published. This check does not skip; pass
  KIP_E2E_PREVIOUS_VERSION=X.Y.Z together with a reachable KIP_RELEASE_BASE_URL."
fi
e2e_note "previous published release: $previous -> candidate: $version"
[[ "$previous" != "$version" ]] || e2e_fail "the previous release and the candidate are both $version; bump VERSION first"

# --------------------------------------------------------------- candidate
e2e_log "Building the candidate package archive"
if [[ -z "$archive" ]]; then
  archive="$work/dist/kip-$version.zip"
  e2e_build_package "$archive"
fi
[[ -f "$archive" && -f "$archive.sha256" ]] || e2e_fail "candidate archive or sidecar missing: $archive"
mirror="$work/releases"
mkdir -p "$mirror/v$version"
cp "$archive" "$archive.sha256" "$mirror/v$version/"

# ------------------------------------------------------------- deployments
home="$work/home"
mkdir -p "$home"
launcher="$home/.local/bin/kip"
deployment_a="$work/deployment-a"
deployment_b="$work/deployment-b"

# Both installs pass --bin-dir under the throwaway HOME: install.sh prefers
# KIP_BIN_DIR over HOME, and the launcher location must not depend on what
# the environment exports. `kip update` below passes no --bin-dir, exactly as
# an operator's does; e2e_clean_env removes KIP_BIN_DIR, so it resolves to the
# same throwaway directory.
e2e_log "Installing the previous release $previous into deployment A"
status=0
e2e_clean_env \
  HOME="$home" SHELL=/bin/bash KIP_SEMANTIC=off \
  KIP_RELEASE_BASE_URL="$release_base" \
  bash "$E2E_PROJECT_ROOT/scripts/install.sh" "$deployment_a" \
    --version "$previous" --without-docker --bin-dir "$home/.local/bin" || status=$?
e2e_assert_status 0 "$status" "install of the previous release $previous"

e2e_log "Installing the candidate into an unrelated deployment B"
# B is the deployment whose global launcher must survive A's update. Its
# installation is the last one to touch the launcher and the shell profile.
status=0
e2e_clean_env \
  HOME="$home" SHELL=/bin/bash KIP_SEMANTIC=off \
  KIP_RELEASE_BASE_URL="file://$mirror" \
  bash "$E2E_PROJECT_ROOT/scripts/install.sh" "$deployment_b" \
    --version "$version" --no-bootstrap --bin-dir "$home/.local/bin" || status=$?
e2e_assert_status 0 "$status" "install of deployment B"
e2e_assert_contains "$launcher" "KIP_DEPLOYMENT='$deployment_b'" "the global launcher now opens deployment B"

profile="$home/.bashrc"
[[ "$(uname -s)" != Darwin ]] || profile="$home/.bash_profile"
launcher_before="$(e2e_digest "$launcher")"
profile_before="$(e2e_digest "$profile")"
e2e_profile_digests "$home" > "$work/throwaway-home.before"

# ------------------------------------------------------------- database
# Before the sentinels below: pointing the deployment at this run's database is
# an edit to a deployment-owned file, and the baseline has to be what the
# upgrade will actually see.
e2e_resolve_database
grep -q "^KIP_DATABASE_URL=" "$deployment_a/.env" \
  || e2e_fail "the installed .env carries no KIP_DATABASE_URL line"
e2e_set_dotenv "$deployment_a/.env" "KIP_DATABASE_URL=$E2E_DATABASE_URL"

# -------------------------------------------------- deployment-owned files
e2e_log "Marking the deployment-owned files an upgrade must never replace"
sentinel='# kip-e2e-sentinel: deployment-owned, must survive an upgrade'
printf '%s\n' "$sentinel" >> "$deployment_a/.env"
printf '%s\n' "$sentinel" >> "$deployment_a/config/kip.toml"
# .mcp.json is JSON that setup owns; mark it without making it invalid.
"$E2E_PYTHON" - "$deployment_a/.mcp.json" <<'MCP'
import json
import sys
from pathlib import Path

target = Path(sys.argv[1])
payload = json.loads(target.read_text(encoding="utf-8"))
payload["kipE2eSentinel"] = "deployment-owned, must survive an upgrade"
target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
MCP
mkdir -p "$deployment_a/var"
printf '%s\n' "$sentinel" > "$deployment_a/var/e2e-sentinel.txt"
owned_before="$work/owned.before"
{
  printf '%s .env\n' "$(e2e_digest "$deployment_a/.env")"
  printf '%s config/kip.toml\n' "$(e2e_digest "$deployment_a/config/kip.toml")"
  printf '%s .mcp.json\n' "$(e2e_digest "$deployment_a/.mcp.json")"
  printf '%s var/e2e-sentinel.txt\n' "$(e2e_digest "$deployment_a/var/e2e-sentinel.txt")"
} > "$owned_before"

# ------------------------------------ skills the previous release installed
# Planted with the PREVIOUS release's own installer, because what an upgrade
# has to carry forward is whatever that release left on disk. Every release
# from 3.5.1 to 3.15.0 ships a byte-identical installer that writes no install
# record, only the global pointer ~/.config/kip/project-root, so a fallback to
# any of them plants the same legacy install. A previous release that already
# writes records (3.15.1 or later) is detected from what it wrote, not from
# its version number, and asserted as a registered refresh instead.
skills_check="$E2E_PROJECT_ROOT/tests/e2e/skill_installs.py"
elsewhere="$home/elsewhere"
legacy_project="$home/projects/legacy-app"
personal_skills="$home/.claude/skills"
project_skills="$legacy_project/.claude/skills"
pointer="$home/.config/kip/project-root"
mkdir -p "$elsewhere" "$legacy_project"

# from_elsewhere COMMAND [ARG ...]: started from an unrelated directory with
# the scrubbed environment and the throwaway HOME, as an agent would.
from_elsewhere() {
  ( cd "$elsewhere" && e2e_clean_env HOME="$home" SHELL=/bin/bash \
      ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} "$@" )
}

e2e_log "Installing agent skills with the previous release's own installer ($previous)"
[[ -f "$deployment_a/scripts/install-agent-files.sh" ]] \
  || e2e_fail "the previous release $previous ships no scripts/install-agent-files.sh (it first shipped in 3.5.1),
  so there is no skill install for this upgrade to carry forward. Pass KIP_E2E_PREVIOUS_VERSION=3.5.1 or newer."
status=0
from_elsewhere "$deployment_a/scripts/install-agent-files.sh" personal > "$work/skills-previous-personal.out" 2>&1 || status=$?
cat "$work/skills-previous-personal.out" >&2
e2e_assert_status 0 "$status" "the $previous installer: install-agent-files.sh personal"
status=0
from_elsewhere "$deployment_a/scripts/install-agent-files.sh" project "$legacy_project" > "$work/skills-previous-project.out" 2>&1 || status=$?
cat "$work/skills-previous-project.out" >&2
e2e_assert_status 0 "$status" "the $previous installer: install-agent-files.sh project $legacy_project"

if [[ -e "$personal_skills/knowledge-fabric/.kip-skill-install" ]]; then
  previous_skills=recorded
  "$E2E_PYTHON" "$skills_check" record "$personal_skills" --deployment "$deployment_a" --version "$previous"
  "$E2E_PYTHON" "$skills_check" record "$project_skills" --deployment "$deployment_a" --version "$previous"
  e2e_note "the $previous installer writes install records: asserting a registered refresh"
else
  previous_skills=legacy
  "$E2E_PYTHON" "$skills_check" unrecorded "$personal_skills"
  "$E2E_PYTHON" "$skills_check" unrecorded "$project_skills"
  [[ "$(cat "$pointer" 2>/dev/null)" == "$deployment_a" ]] \
    || e2e_fail "the $previous installer did not record $deployment_a in the legacy pointer $pointer"
  [[ ! -e "$deployment_a/var/skill-installs.json" ]] \
    || e2e_fail "the $previous installer already wrote a skill install registry"
  e2e_note "the $previous installer left a legacy install: no records, the pointer names deployment A"
fi
pointer_before="$(e2e_digest "$pointer")"

# ---------------------------------------------------------------- upgrade
run_a() {
  e2e_clean_env HOME="$home" SHELL=/bin/bash \
    ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} \
    "$deployment_a/scripts/kip" "$@"
}

e2e_log "Upgrading deployment A with kip update --archive"
status=0
run_a update --archive "$archive" || status=$?
e2e_assert_status 0 "$status" "kip update --archive $previous -> $version"

[[ "$(tr -d '[:space:]' < "$deployment_a/VERSION")" == "$version" ]] \
  || e2e_fail "deployment A reports $(cat "$deployment_a/VERSION") after the upgrade, expected $version"
e2e_note "version moved: $previous -> $version"

owned_after="$work/owned.after"
{
  printf '%s .env\n' "$(e2e_digest "$deployment_a/.env")"
  printf '%s config/kip.toml\n' "$(e2e_digest "$deployment_a/config/kip.toml")"
  printf '%s .mcp.json\n' "$(e2e_digest "$deployment_a/.mcp.json")"
  printf '%s var/e2e-sentinel.txt\n' "$(e2e_digest "$deployment_a/var/e2e-sentinel.txt")"
} > "$owned_after"
e2e_assert_same_profiles "$owned_before" "$owned_after" \
  "deployment-owned files (.env, config/kip.toml, .mcp.json, var/) survived the upgrade"

[[ "$(e2e_digest "$launcher")" == "$launcher_before" ]] \
  || e2e_fail "kip update rewrote the global launcher, which opened the unrelated deployment B ($deployment_b)"
e2e_assert_contains "$launcher" "KIP_DEPLOYMENT='$deployment_b'" "the launcher still opens deployment B"
[[ "$(e2e_digest "$profile")" == "$profile_before" ]] \
  || e2e_fail "kip update modified the shell profile $profile, which it was not asked to touch"
e2e_profile_digests "$home" > "$work/throwaway-home.after"
e2e_assert_same_profiles "$work/throwaway-home.before" "$work/throwaway-home.after" \
  "no shell profile under the throwaway HOME was modified by the upgrade"
e2e_assert_real_home_unchanged \
  || e2e_fail "the install or upgrade changed this machine's own kip launcher or shell profile"

# ------------------------------------ the upgrade carried the skills forward
e2e_log "The skill copies the previous release installed follow the upgrade (ADR-067)"
if [[ "$previous_skills" == legacy ]]; then
  # `kip update --archive` from a release without refresh_skills does not
  # adopt: upgrade_package.py swaps files with os.replace, so the running
  # previous upgrade.sh finishes with its own finish_upgrade, which predates
  # the refresh (3.15.0 -> 3.15.1, measured). Only the download path runs the
  # new tree's `upgrade.sh --finish`. What the release guarantees for an
  # archive upgrade from such a release is the one-time refresh below; the
  # state the archive step left is reported either way.
  if [[ -e "$personal_skills/knowledge-fabric/.kip-skill-install" ]]; then
    e2e_note "kip update --archive from $previous adopted the legacy personal copy itself"
  else
    e2e_note "kip update --archive from $previous did not adopt the legacy personal copy (its own finish step has no refresh); running the one-time refresh"
  fi
  status=0
  from_elsewhere "$deployment_a/scripts/install-agent-files.sh" --refresh > "$work/skills-refresh.out" 2>&1 || status=$?
  cat "$work/skills-refresh.out" >&2
  e2e_assert_status 0 "$status" "one-time install-agent-files.sh --refresh after the archive upgrade"
fi
"$E2E_PYTHON" "$skills_check" record "$personal_skills" --deployment "$deployment_a" --version "$version" \
  --client claude --scope personal
if [[ "$previous_skills" == legacy ]]; then
  e2e_note "adopted: the legacy personal copy now records $version"
  [[ "$(e2e_digest "$pointer")" == "$pointer_before" ]] \
    || e2e_fail "the upgrade changed the legacy pointer $pointer; record-less project copies still resolve through it"
  e2e_note "unchanged: the legacy pointer $pointer"
  # A 3.15.0 project copy left no trace an upgrade can find: it must be left
  # alone and must still answer from deployment A through the pointer.
  "$E2E_PYTHON" "$skills_check" unrecorded "$project_skills"
  "$E2E_PYTHON" "$skills_check" registry "$deployment_a" --expect "$personal_skills"
else
  "$E2E_PYTHON" "$skills_check" record "$project_skills" --deployment "$deployment_a" --version "$version" \
    --client claude --scope project
  [[ ! -e "$pointer" ]] || e2e_fail "the upgrade wrote the legacy pointer $pointer"
  "$E2E_PYTHON" "$skills_check" registry "$deployment_a" --expect "$personal_skills" --expect "$project_skills"
fi
for copy in "$personal_skills" "$project_skills"; do
  status=0
  from_elsewhere "$copy/knowledge-fabric/scripts/kip.sh" capabilities > "$work/copy-capabilities.json" || status=$?
  e2e_assert_status 0 "$status" "$copy/knowledge-fabric/scripts/kip.sh capabilities from an unrelated directory"
  "$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" capabilities "$work/copy-capabilities.json" \
    --repository postgresql --no-expect-semantic
  status=0
  from_elsewhere "$copy/knowledge-fabric/scripts/kip.sh" doctor > "$work/copy-doctor.json" || status=$?
  e2e_assert_status 0 "$status" "$copy/knowledge-fabric/scripts/kip.sh doctor from an unrelated directory"
  "$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" doctor "$work/copy-doctor.json" --deployment "$deployment_a"
done

# The launcher defect lived in the download path (`--latest` / `--version`),
# which reaches install.sh, not in `--archive`, which does not. Exercise it
# too: the deployment is already current, so this is the cheap
# "nothing to do" branch that still calls the launcher refresh.
e2e_log "Re-running the download path (kip update --version) on the current deployment"
status=0
e2e_clean_env HOME="$home" SHELL=/bin/bash KIP_RELEASE_BASE_URL="file://$mirror" \
  ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} \
  "$deployment_a/scripts/kip" update --version "$version" || status=$?
e2e_assert_status 0 "$status" "kip update --version on an already-current deployment"
[[ "$(e2e_digest "$launcher")" == "$launcher_before" ]] \
  || e2e_fail "the download update path repointed the global launcher away from deployment B"
[[ "$(e2e_digest "$profile")" == "$profile_before" ]] \
  || e2e_fail "the download update path modified the shell profile $profile"
e2e_note "the download update path left the launcher and the profile alone"

# ------------------------------------------------ the upgraded deployment works
e2e_log "The upgraded deployment still answers"
status=0
run_a version > "$work/version.json" || status=$?
e2e_assert_status 0 "$status" "kip version after the upgrade"
"$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" version "$work/version.json" --expect "$version"

status=0
run_a sync run --source sample > "$work/sync.json" || status=$?
e2e_assert_status 0 "$status" "kip sync run --source sample after the upgrade"
status=0
run_a search "정산" --limit 5 > "$work/search.json" || status=$?
e2e_assert_status 0 "$status" "kip search after the upgrade"
"$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" search "$work/search.json" --min-hits 2

# ----------------------------------------- a stale registered copy is refreshed
# Nothing is bumped: one registered copy is made to look like an older install
# left it, and the upgrade's own finish step has to notice and reinstall it.
e2e_log "upgrade.sh --finish refreshes a registered copy that went stale"
"$E2E_PYTHON" "$skills_check" set-version "$personal_skills" --skill knowledge-fabric --version 0.0.0-e2e-stale
status=0
run_a doctor > "$work/doctor-stale.json" || status=$?
e2e_assert_status 0 "$status" "kip doctor with a stale registered copy"
"$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" doctor "$work/doctor-stale.json" \
  --deployment "$deployment_a" --check-not-ok skill_installs
status=0
e2e_clean_env HOME="$home" SHELL=/bin/bash \
  ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} \
  "$deployment_a/scripts/upgrade.sh" --finish > "$work/finish.out" 2>&1 || status=$?
cat "$work/finish.out" >&2
e2e_assert_status 0 "$status" "upgrade.sh --finish"
"$E2E_PYTHON" "$skills_check" record "$personal_skills" --deployment "$deployment_a" --version "$version" \
  --client claude --scope personal
e2e_assert_contains "$work/finish.out" "Refreshed $personal_skills" "the finish step reports the refresh"
status=0
run_a doctor > "$work/doctor-refreshed.json" || status=$?
e2e_assert_status 0 "$status" "kip doctor after the refresh"
"$E2E_PYTHON" "$E2E_PROJECT_ROOT/tests/e2e/kip_envelope.py" doctor "$work/doctor-refreshed.json" \
  --deployment "$deployment_a" --check-ok skill_installs
[[ "$previous_skills" != legacy || "$(e2e_digest "$pointer")" == "$pointer_before" ]] \
  || e2e_fail "the refresh changed the legacy pointer $pointer"
[[ "$(e2e_digest "$launcher")" == "$launcher_before" ]] \
  || e2e_fail "the skill refresh repointed the global launcher away from deployment B"

e2e_log "PASS: $previous upgrades to $version in place, only package-owned files moved, and installed skills followed"
