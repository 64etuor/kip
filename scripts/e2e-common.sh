#!/usr/bin/env bash
# Shared helpers for the end-to-end checks in scripts/e2e-*.sh.
#
# These scripts deliberately do NOT source scripts/common.sh. common.sh loads
# this checkout's .env and exports KIP_CONFIG, PYTHONPATH and PATH, which is
# exactly the contamination an installer check must not have: the whole point
# is to observe what an operator gets from the shipped artifact alone. Each
# e2e script therefore computes its own PROJECT_ROOT, scrubs inherited KIP_*
# variables, and drives the installed deployment through its own entry points.
#
# Requires bash 4+ features only where noted; macOS bash 3.2 runs these too.

# shellcheck shell=bash

E2E_STARTED_AT="$(date +%s)"
e2e_elapsed() {
  local seconds=$(( $(date +%s) - E2E_STARTED_AT ))
  printf '%dm%02ds' $(( seconds / 60 )) $(( seconds % 60 ))
}
e2e_log() { printf '\n== [%s] %s\n' "$(e2e_elapsed)" "$*" >&2; }
e2e_note() { printf '   %s\n' "$*" >&2; }
e2e_fail() { printf 'e2e: %s\n' "$*" >&2; exit 1; }

# e2e_clean_env [VAR=VALUE ...] COMMAND [ARG ...]
# Run COMMAND with every inherited variable that could steer a deployment under
# test removed, then the given assignments applied. Removed by PREFIX, not from
# a list, because a list goes stale the moment a new variable is read:
#   KIP_*       every KIP setting, including KIP_BIN_DIR (install.sh writes the
#               launcher there), KIP_API_PORT / KIP_POSTGRES_PORT and
#               KIP_NAS_PATH (Compose prefers the shell over .env, so these
#               would bind a live port or mount a real NAS) and KIP_SKIP_DOTENV;
#   POSTGRES_*  interpolated by compose.yaml;
#   COMPOSE_*   COMPOSE_FILE / COMPOSE_PROJECT_NAME would redirect Compose;
#   PG*         libpq defaults (PGHOST, PGDATABASE, ...) a URL does not override;
#   ZDOTDIR     where install.sh looks for a zsh profile;
#   PYTHONPATH, VIRTUAL_ENV  this checkout's interpreter;
#   BASH_ENV, ENV  startup files a child bash reads, which could re-export any of the above;
#   CLAUDE_PROJECT_DIR  an installed skill's scripts/kip.sh walks up from it
#               before reading its own install record, so a run started from an
#               agent session inside a KIP checkout would answer from THAT
#               checkout and its live database;
#   CODEX_HOME  where Codex looks for its own state instead of HOME.
e2e_clean_env() {
  local unsets=() name
  while IFS= read -r name; do
    case "$name" in
      KIP_*|POSTGRES_*|COMPOSE_*|PG*|ZDOTDIR|PYTHONPATH|VIRTUAL_ENV|BASH_ENV|ENV|CLAUDE_PROJECT_DIR|CODEX_HOME) unsets+=(-u "$name") ;;
    esac
  done < <(compgen -e)
  env ${unsets[@]+"${unsets[@]}"} "$@"
}

# e2e_retry ATTEMPTS DESCRIPTION COMMAND [ARG ...]
# Bounded retry with backoff (5s, 10s, 20s ...) for a download from a registry
# or release host, so one transient network error does not fail a release.
# Every failed attempt is reported; the last one fails.
# e2e_run_with_limit SECONDS COMMAND [ARG ...]
# Runs COMMAND (a shell function works too) and stops it after SECONDS. A
# stalled registry answered a `docker compose pull` with silence for twelve
# minutes; without a limit a job only ends at the workflow timeout. The
# command's direct children are signalled as well, since stopping only the
# subshell would leave the docker CLI running.
e2e_run_with_limit() {
  local limit="$1" pid watchdog status=0
  shift
  ( "$@" ) &
  pid=$!
  # The watchdog's own sleep is killed on the success path, and bash would
  # report that as "Terminated" on every retry; its stderr carries nothing else.
  (
    sleep "$limit"
    pkill -TERM -P "$pid" 2>/dev/null
    kill -TERM "$pid" 2>/dev/null
  ) 2>/dev/null &
  watchdog=$!
  wait "$pid" || status=$?
  pkill -TERM -P "$watchdog" 2>/dev/null
  kill -TERM "$watchdog" 2>/dev/null
  wait "$watchdog" 2>/dev/null || true
  return "$status"
}

e2e_retry() {
  local attempts="$1" description="$2" attempt=1 delay=5
  local limit="${E2E_RETRY_ATTEMPT_SECONDS:-300}"
  shift 2
  until e2e_run_with_limit "$limit" "$@"; do
    if (( attempt >= attempts )); then
      printf 'e2e: %s failed %d times; giving up\n' "$description" "$attempts" >&2
      return 1
    fi
    e2e_note "$description failed (attempt $attempt of $attempts); retrying in ${delay}s"
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

# ------------------------------------------------ this machine's real HOME
# A run must never write the operator's own launcher or shell profile. Every
# child already gets a throwaway HOME, an explicit --bin-dir and a scrubbed
# environment; this is the independent proof. The digests are taken HERE, when
# the script sources this file and before it writes anything, and
# e2e_assert_real_home_unchanged compares them again (each script calls it
# from its EXIT trap, after its own cleanup, and fails the run on a change).
E2E_REAL_HOME="${HOME:-/nonexistent}"
e2e_real_home_paths() {
  local zdotdir="${ZDOTDIR:-}" name
  if [[ -z "$zdotdir" ]] && command -v zsh >/dev/null 2>&1; then
    # ZDOTDIR is usually set in ~/.zshenv without export; install.sh asks zsh
    # the same way.
    zdotdir="$(zsh -c 'printf %s "${ZDOTDIR:-}"' 2>/dev/null </dev/null || true)"
  fi
  printf '%s\n' "$E2E_REAL_HOME/.local/bin/kip"
  # An exported KIP_BIN_DIR is where install.sh would have written a launcher.
  [[ -z "${KIP_BIN_DIR:-}" ]] || printf '%s\n' "$KIP_BIN_DIR/kip"
  for name in .bashrc .bash_profile .bash_login .profile .zshenv .zprofile .zshrc .zlogin; do
    printf '%s\n' "$E2E_REAL_HOME/$name"
    [[ -z "$zdotdir" || "$zdotdir" == "$E2E_REAL_HOME" ]] || printf '%s\n' "$zdotdir/$name"
  done
}

# Directories an agent-skill install or uninstall writes (ADR-067): the KIP
# skill trees for each client and the legacy global pointer. Hashed as whole
# trees, so a run that installed, refreshed, adopted or removed a real copy
# fails even when it put the same bytes back.
e2e_real_home_trees() {
  local skills name
  for skills in .claude/skills .agents/skills; do
    for name in knowledge-fabric kip-setup; do
      printf '%s\n' "$E2E_REAL_HOME/$skills/$name"
    done
  done
}

e2e_real_home_digests() {
  local path
  while IFS= read -r path; do
    printf '%s %s\n' "$(e2e_digest "$path")" "$path"
  done < <(e2e_real_home_paths; printf '%s\n' "$E2E_REAL_HOME/.config/kip/project-root")
  while IFS= read -r path; do
    printf '%s %s\n' "$(e2e_tree_digest "$path")" "$path"
  done < <(e2e_real_home_trees)
}

e2e_assert_real_home_unchanged() {
  local after
  after="$(e2e_real_home_digests)"
  if [[ "$after" != "$E2E_REAL_HOME_BEFORE" ]]; then
    diff <(printf '%s\n' "$E2E_REAL_HOME_BEFORE") <(printf '%s\n' "$after") >&2 || true
    printf 'e2e: this run changed the kip launcher or a shell profile of the real HOME %s\n' "$E2E_REAL_HOME" >&2
    return 1
  fi
  e2e_note "unchanged: the real HOME's kip launcher and shell profiles ($(wc -l <<< "$after" | tr -d ' ') paths)"
}

# A throwaway HOME exists to protect the operator's SHELL PROFILE; it must not
# hide the Docker they already have. On a developer machine the Compose CLI
# plugin and the daemon context live under $HOME/.docker, so an installer run
# with HOME redirected would report Docker as missing. Point DOCKER_CONFIG back
# at the real directory. A Linux CI runner has no such directory and needs
# nothing here.
E2E_DOCKER_CONFIG=()
if [[ -z "${DOCKER_CONFIG:-}" && -d "${HOME:-/nonexistent}/.docker" ]]; then
  # shellcheck disable=SC2034  # read by the e2e-*.sh scripts that source this file
  E2E_DOCKER_CONFIG=(DOCKER_CONFIG="$HOME/.docker")
fi

e2e_require_command() {
  command -v "$1" >/dev/null 2>&1 || e2e_fail "$1 is required for this check but is not on PATH"
}

e2e_free_port() {
  "${E2E_PYTHON:-python3}" -c 'import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()'
}

# The PostgreSQL image this repository pins, read from compose.yaml so a digest
# bump moves the e2e checks with the deployment instead of drifting from it.
e2e_postgres_image() {
  if [[ -n "${KIP_E2E_POSTGRES_IMAGE:-}" ]]; then
    printf '%s\n' "$KIP_E2E_POSTGRES_IMAGE"
    return 0
  fi
  local image
  image="$(sed -n 's/.*KIP_POSTGRES_IMAGE:-\(pgvector[^}]*\)}.*/\1/p' "$E2E_PROJECT_ROOT/compose.yaml" | head -n 1)"
  [[ -n "$image" ]] || e2e_fail "could not read the pinned PostgreSQL image from compose.yaml"
  printf '%s\n' "$image"
}

# Start a throwaway PostgreSQL container and set E2E_DATABASE_URL.
# Never reuses a deployment's container or database: the name carries this
# process id, the port is free, and e2e_stop_postgres removes both.
E2E_POSTGRES_CONTAINER=""
E2E_POSTGRES_PORT=""
# Absolute host paths to bind into the throwaway container at the SAME path.
# The client tools run inside that container (see e2e_container_clients), so a
# --file or -f argument has to name a path that exists on both sides.
E2E_POSTGRES_MOUNTS=()
e2e_start_postgres() {
  e2e_require_command docker
  local name="kip-e2e-postgres-$$" port image attempt mount mounts=()
  port="$(e2e_free_port)"
  image="$(e2e_postgres_image)"
  for mount in ${E2E_POSTGRES_MOUNTS[@]+"${E2E_POSTGRES_MOUNTS[@]}"}; do
    mounts+=(--volume "$mount:$mount")
  done
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    e2e_retry 4 "docker pull $image" docker pull --quiet "$image" >/dev/null \
      || e2e_fail "could not pull $image from its registry"
  fi
  e2e_note "starting throwaway PostgreSQL $name on 127.0.0.1:$port"
  # Recorded BEFORE `docker run`: a run that fails after creating the container
  # (a port race, a bad mount) must still be removed by e2e_stop_postgres.
  E2E_POSTGRES_CONTAINER="$name"
  docker run --detach --name "$name" \
    --env POSTGRES_DB=kip \
    --env POSTGRES_USER=kip_owner \
    --env POSTGRES_PASSWORD=test-password \
    --publish "127.0.0.1:$port:5432" \
    ${mounts[@]+"${mounts[@]}"} \
    "$image" >/dev/null
  E2E_POSTGRES_PORT="$port"
  # Probe over TCP and require three consecutive answers. The image's first
  # start runs initdb against a temporary server that listens only on the Unix
  # socket and then restarts; a socket probe could pass in that window, and
  # the host's first connection then failed with "server closed the
  # connection unexpectedly" (3.15.4 tag CI).
  attempt=0
  local ready=0
  until (( ready >= 3 )); do
    if docker exec "$name" pg_isready -h 127.0.0.1 -p 5432 -U kip_owner -d kip >/dev/null 2>&1; then
      ready=$((ready + 1))
    else
      ready=0
    fi
    attempt=$((attempt + 1))
    (( attempt < 90 )) || e2e_fail "throwaway PostgreSQL $name never became ready"
    sleep 1
  done
  E2E_DATABASE_URL="postgresql://kip_owner:test-password@127.0.0.1:$port/kip"
}

e2e_stop_postgres() {
  [[ -n "$E2E_POSTGRES_CONTAINER" ]] || return 0
  docker rm --force --volumes "$E2E_POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  E2E_POSTGRES_CONTAINER=""
}

# Install and upgrade MIGRATE and SYNC the database they are given, so a
# supplied URL that names a live database would write into it. Refuse both
# shapes a live database has on a developer machine: the database `kip` on the
# default local host and port (what compose.yaml and bootstrap create), and the
# host, port and database this checkout's own .env KIP_DATABASE_URL names,
# wherever that is. Compared on host, port and database name, so a different
# password or user spelling does not slip past.
e2e_refuse_live_database() {
  "${E2E_PYTHON:-python3}" - "$1" "$E2E_PROJECT_ROOT/.env" <<'PY'
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

LOCAL = {"", "localhost", "127.0.0.1", "::1"}


def target(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    host = "localhost" if host in LOCAL else host
    # libpq defaults the database name to the user name.
    database = unquote(parts.path.lstrip("/")) or unquote(parts.username or "")
    return host, parts.port or 5432, database


supplied = target(sys.argv[1])
if supplied == ("localhost", 5432, "kip"):
    raise SystemExit(
        "e2e: refusing KIP_E2E_DATABASE_URL: it names the database kip on the default "
        "local host and port, which is where a live deployment keeps its data. "
        "Supply a disposable database, or unset it to start a throwaway container."
    )
dotenv = Path(sys.argv[2])
if dotenv.is_file():
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "KIP_DATABASE_URL" and value.strip().strip("'\""):
            if target(value.strip().strip("'\"")) == supplied:
                raise SystemExit(
                    "e2e: refusing KIP_E2E_DATABASE_URL: it names the same host, port and "
                    f"database as KIP_DATABASE_URL in {dotenv}, this checkout's own database."
                )
PY
}

# Resolve the database this run uses: an externally supplied one (a CI service
# container) or a throwaway container started here so the check is one command
# on a developer machine.
e2e_resolve_database() {
  if [[ -n "${KIP_E2E_DATABASE_URL:-}" ]]; then
    e2e_refuse_live_database "$KIP_E2E_DATABASE_URL"
    # shellcheck disable=SC2034  # read by the caller that sourced this file
    E2E_DATABASE_URL="$KIP_E2E_DATABASE_URL"
    e2e_note "using the supplied KIP_E2E_DATABASE_URL"
    return 0
  fi
  e2e_start_postgres
}

# PostgreSQL client tools that match the server.
# pg_dump refuses a server newer than itself and this repository pins
# PostgreSQL 18, so a host with an older psql/pg_dump (or none at all, which is
# the normal state of a macOS developer machine) cannot run the backup half of
# the role check. These wrappers run the server image's own client binaries
# through `docker exec`, as the calling user so the files they write stay
# readable, and rewrite the published port back to the container's 5432. Both
# the URL and the wrapper are generated here, so the substitution is exact.
# Put the directory first on PATH: deploy/apply-roles.sh calls `psql`, and
# scripts/postgres-tools.sh resolves `psql`, `pg_dump` and `pg_restore` from
# PATH unless PSQL/PG_DUMP/PG_RESTORE name something else.
e2e_container_clients() {
  local bindir="$1" tool template
  [[ -n "$E2E_POSTGRES_CONTAINER" ]] || e2e_fail "e2e_container_clients needs a throwaway container"
  mkdir -p "$bindir"
  template="$bindir/.wrapper.template"
  # Quoted heredoc: the body below is the WRAPPER's script, not this one's.
  # The three placeholders are substituted per tool.
  cat > "$template" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
# Generated by scripts/e2e-common.sh. Runs @@TOOL@@ from the throwaway
# PostgreSQL container, so the client and the server are the same major
# version, as the calling user so the files it writes stay readable.
translated=()
for argument in "$@"; do
  translated+=("${argument//127.0.0.1:@@PORT@@/127.0.0.1:5432}")
done
# libpq reads these from the environment, and `docker exec` forwards nothing
# by default. scripts/backup.sh and scripts/restore.sh set PGOPTIONS
# (`-c row_security=off`) this way, and a dropped PGOPTIONS makes a backup
# silently workspace-filtered - the exact failure those scripts exist to
# prevent - so losing one here has to be impossible, not unlikely.
forwarded=()
for name in PGOPTIONS PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE PGSSLMODE PGCONNECT_TIMEOUT PGAPPNAME; do
  value="${!name:-}"
  [[ -n "$value" ]] || continue
  case "$name" in
    PGHOST) [[ "$value" != 127.0.0.1 && "$value" != localhost ]] || value=127.0.0.1 ;;
    PGPORT) [[ "$value" != "@@PORT@@" ]] || value=5432 ;;
  esac
  forwarded+=(--env "$name=$value")
done
exec docker exec --interactive --user "$(id -u):$(id -g)" \
  ${forwarded[@]+"${forwarded[@]}"} \
  @@CONTAINER@@ @@TOOL@@ ${translated[@]+"${translated[@]}"}
WRAPPER
  for tool in psql pg_dump pg_restore pg_isready; do
    sed -e "s/@@PORT@@/$E2E_POSTGRES_PORT/g" \
        -e "s/@@CONTAINER@@/$E2E_POSTGRES_CONTAINER/g" \
        -e "s/@@TOOL@@/$tool/g" "$template" > "$bindir/$tool"
    chmod 755 "$bindir/$tool"
  done
  rm -f "$template"
  e2e_note "using the $E2E_POSTGRES_CONTAINER client tools from $bindir"
}

# Build the package archive the release publishes, into a path this run owns.
# scripts/build-package.sh refuses to overwrite, and dist/ holds real release
# artifacts, so every e2e run builds into its own work tree.
e2e_build_package() {
  # e2e_build_package OUTPUT_ZIP
  local output="$1" build_args=()
  mkdir -p "$(dirname "$output")"
  if [[ -n "$(git -C "$E2E_PROJECT_ROOT" status --porcelain 2>/dev/null || true)" ]]; then
    # The publish job builds a clean tag. A developer running this check has
    # edits in the tree, which the package safety policy otherwise refuses.
    e2e_note "working tree is dirty: building with --allow-dirty (CI builds the clean tag)"
    build_args+=(--allow-dirty)
  fi
  "$E2E_PROJECT_ROOT/scripts/build-package.sh" --output "$output" \
    ${build_args[@]+"${build_args[@]}"} > "$output.receipt.json"
  [[ -f "$output" && -f "$output.sha256" ]] \
    || e2e_fail "the package build produced no archive or sidecar at $output"
  e2e_note "archive: $output"
}

# Set keys in a deployment's .env the way an operator edits it: replace the
# existing assignment, or append when the key is absent. Never append a second
# line for a key that is already there - scripts/load_dotenv.py rejects a
# duplicate key and every script in the deployment then fails.
e2e_set_dotenv() {
  # e2e_set_dotenv ENV_FILE KEY=VALUE [KEY=VALUE ...]
  "${E2E_PYTHON:-python3}" -c '
import sys
from pathlib import Path

target = Path(sys.argv[1])
assignments = {}
for argument in sys.argv[2:]:
    key, separator, value = argument.partition("=")
    if not separator:
        raise SystemExit(f"e2e_set_dotenv expects KEY=VALUE, got {argument!r}")
    assignments[key] = value
lines = target.read_text(encoding="utf-8").splitlines()
for index, line in enumerate(lines):
    key = line.split("=", 1)[0].strip()
    if key in assignments:
        lines[index] = f"{key}={assignments.pop(key)}"
lines.extend(f"{key}={value}" for key, value in assignments.items())
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
' "$@"
}

# Point a bootstrapped .env at this run's throwaway (or supplied) database.
# Bootstrap writes KIP_POSTGRES_PORT=5432 and matching URLs; the e2e database
# is on another loopback port, which the 3.15.5 port guard refuses unless the
# published port and both URLs agree.
e2e_point_deployment_at_database() {
  local env_file="$1" port
  port="${E2E_POSTGRES_PORT:-}"
  if [[ -z "$port" ]]; then
    port="$("${E2E_PYTHON:-python3}" -c 'from urllib.parse import urlsplit; import sys; print(urlsplit(sys.argv[1]).port or 5432)' "$E2E_DATABASE_URL")"
  fi
  e2e_set_dotenv "$env_file" \
    "KIP_DATABASE_URL=$E2E_DATABASE_URL" \
    "KIP_BACKUP_DATABASE_URL=$E2E_DATABASE_URL" \
    "KIP_POSTGRES_PORT=$port"
}

e2e_digest() {
  # sha256 of a file, or the literal "absent". Used to prove a path this run
  # did not ask to change was not changed.
  local path="$1"
  if [[ ! -e "$path" ]]; then printf 'absent\n'; return 0; fi
  if [[ ! -f "$path" ]]; then printf 'not-a-regular-file\n'; return 0; fi
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$path" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$path" | awk '{print $1}'
  else e2e_fail "sha256sum or shasum is required"; fi
}

e2e_tree_digest() {
  # sha256 over every regular file's relative path and content under DIR, or
  # "absent". Proves a directory this run planted and did not ask to change
  # (a foreign skill of the same name) was left exactly as it was.
  "${E2E_PYTHON:-python3}" - "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not root.is_dir():
    print("absent")
    raise SystemExit(0)
digest = hashlib.sha256()
for path in sorted(item for item in root.rglob("*") if item.is_file() or item.is_symlink()):
    digest.update(str(path.relative_to(root)).encode() + b"\0")
    digest.update(b"link:" + str(path.readlink()).encode() if path.is_symlink() else path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

# The shell-profile files install.sh could choose on this platform, for the
# HOME it is given. Capturing their digests before and after a run is how these
# checks prove the installer did not touch a profile it was not asked to.
e2e_profile_paths() {
  local home="$1"
  printf '%s\n' "$home/.bashrc" "$home/.bash_profile" "$home/.bash_login" "$home/.profile" \
    "$home/.zshenv" "$home/.zprofile" "$home/.zshrc" "$home/.zlogin"
}

e2e_profile_digests() {
  local home="$1" path
  while IFS= read -r path; do
    printf '%s %s\n' "$(e2e_digest "$path")" "$path"
  done < <(e2e_profile_paths "$home")
}

e2e_assert_same_profiles() {
  # e2e_assert_same_profiles BEFORE_FILE AFTER_FILE DESCRIPTION
  if ! diff -u "$1" "$2" >/dev/null; then
    diff -u "$1" "$2" >&2 || true
    e2e_fail "$3"
  fi
  e2e_note "unchanged: $3"
}

e2e_assert_contains() {
  # e2e_assert_contains FILE FIXED_STRING DESCRIPTION
  grep -qF -- "$2" "$1" || e2e_fail "$3 (expected $1 to contain: $2)"
  e2e_note "confirmed: $3"
}

e2e_assert_status() {
  # e2e_assert_status EXPECTED ACTUAL DESCRIPTION
  [[ "$1" == "$2" ]] || e2e_fail "$3: expected exit code $1, got $2"
  e2e_note "exit $2: $3"
}

# Run a command, capture stdout to a file, and return its exit status without
# tripping `set -e` in the caller.
e2e_capture() {
  # e2e_capture OUTPUT_FILE COMMAND...
  local output="$1"; shift
  local status=0
  "$@" > "$output" 2> "$output.stderr" || status=$?
  printf '%s\n' "$status"
}

# The real-HOME baseline, taken as the last thing this file does so that every
# helper it needs is defined and nothing the sourcing script does can precede it.
E2E_REAL_HOME_BEFORE="$(e2e_real_home_digests)"
