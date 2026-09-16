#!/usr/bin/env bash
# End-to-end check of what 3.13.0 claimed about database roles, and of the
# identity configuration of the production Compose file it shipped.
#
# Three modes, because the claims cost very different amounts to prove:
#
#   --mode database   (default; measured 10s once the container is up, and
#                      4-6 minutes in CI where the locked environment installs
#                      first)
#     Migrate a throwaway PostgreSQL, apply deploy/sql/roles.sql.template the
#     way deploy/apply-roles.sh does, and then prove:
#       * kip_api and kip_worker exist and are NOT superusers and do NOT
#         bypass row level security, while kip_backup does bypass it;
#       * a kip_api session in workspace alpha finds nothing of workspace beta
#         through the application, and in raw SQL reads no beta row from any
#         table in the database that FORCEs row level security (what migration
#         0028 applies) and has a workspace_id column - listed from the
#         catalog, not from this script;
#       * scripts/backup.sh taken as the kip_backup login produces a non-empty
#         dump, and scripts/restore.sh loads it into an empty database and
#         verifies its projections.
#     NOT covered here: that the api and worker CONTAINERS are the ones using
#     those roles, and that any API container boots.
#
#   --mode production-config   (measured 1s; starts no container)
#     Render compose.production.yaml with `docker compose config`, using
#     placeholder image references and JWT settings and generated secret
#     files, and feed the api, worker and migrate services' resolved
#     environment into Settings.load() and the identity construction in
#     src/kip/container.py (tests/e2e/production_identity.py); all three build
#     the container before doing anything. 3.13.0 shipped that file with an
#     identity mode the code rejects, so its API could not start; this mode
#     fails on exactly that, and proves it can by requiring the api
#     environment with the mode 3.13.0 shipped (`jwt`) to be rejected.
#     Needs this checkout's locked environment (.venv).
#     NOT covered: booting compose.production.yaml. Nothing here starts its
#     containers, reads its real secrets, reaches a JWKS issuer or asks /readyz.
#
#   --mode compose    (measured 19s against a warm image cache, 9-10 minutes
#                      on the first run, which builds the runtime image)
#     Bring up compose.yaml plus deploy/compose.roles.yaml - the application
#     profile ./scripts/app-up.sh starts, api_key identity from .env - from a
#     deployment installed out of the candidate package, and prove:
#       * that API answers /readyz;
#       * the api and worker backends in pg_stat_activity are kip_api and
#         kip_worker, not the bootstrap superuser.
#     This is NOT compose.production.yaml, whose proxy_jwt identity, secrets
#     and pinned images are only checked, without booting, by production-config.
#     It never touches an existing deployment: the Compose project name, the
#     ports and the volumes all belong to this run and are removed afterwards.
#
#   ./scripts/e2e-db-roles.sh
#   ./scripts/e2e-db-roles.sh --mode production-config
#   ./scripts/e2e-db-roles.sh --mode compose
#
# Semantic search is forced off: this check must never start, stop or reuse a
# model runtime that belongs to the machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=e2e-common.sh
source "$SCRIPT_DIR/e2e-common.sh"

mode=database
keep_work=0
work=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) [[ $# -ge 2 ]] || e2e_fail "--mode needs database, production-config or compose"; mode="$2"; shift 2 ;;
    --mode=*) mode="${1#--mode=}"; shift ;;
    --keep) keep_work=1; shift ;;
    --work) [[ $# -ge 2 ]] || e2e_fail "--work needs a value"; work="$2"; shift 2 ;;
    -h|--help) sed -n '2,56p' "$0"; exit 0 ;;
    *) e2e_fail "unknown option: $1" ;;
  esac
done
[[ "$mode" == database || "$mode" == production-config || "$mode" == compose ]] \
  || e2e_fail "--mode must be database, production-config or compose"

E2E_PYTHON="${KIP_E2E_PYTHON:-python3}"
e2e_require_command "$E2E_PYTHON"
e2e_require_command docker
version="$(tr -d '[:space:]' < "$E2E_PROJECT_ROOT/VERSION")"

if [[ -z "$work" ]]; then work="$(mktemp -d "${TMPDIR:-/tmp}/kip-e2e-roles.XXXXXX")"; fi
mkdir -p "$work"
# -P: the physical path. Scripts under test resolve symlinks (backup.sh
# uses `pwd -P`), and on macOS /tmp is a symlink, so a logical path here would
# name a directory that is not the one bind-mounted into a container.
work="$(cd "$work" && pwd -P)"
COMPOSE_PROJECT=""
DEPLOYMENT=""
cleanup() {
  local status=$?
  if [[ -n "$COMPOSE_PROJECT" && -n "$DEPLOYMENT" ]]; then
    # A container that exited is the whole diagnosis, and `down` destroys it.
    if [[ "$status" != 0 ]]; then
      printf '\n-- compose logs (this run only) --\n' >&2
      ( cd "$DEPLOYMENT" && e2e_clean_env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" \
          docker compose -f compose.yaml -f deploy/compose.roles.yaml --profile app \
          logs --tail 80 ) >&2 2>&1 || true
    fi
    ( cd "$DEPLOYMENT" && e2e_clean_env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" \
        docker compose -f compose.yaml -f deploy/compose.roles.yaml --profile app \
        down --volumes --remove-orphans --rmi local >/dev/null 2>&1 ) || true
  fi
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

# =========================================================== database mode
run_database_mode() {
  local owner restore_url
  # shellcheck disable=SC2034  # read by e2e_start_postgres in e2e-common.sh
  E2E_POSTGRES_MOUNTS=("$work" "$E2E_PROJECT_ROOT")
  e2e_start_postgres
  owner="$E2E_DATABASE_URL"
  e2e_container_clients "$work/bin"
  export PATH="$work/bin:$PATH"

  # Distinct logins for the three group roles, so a cross-role mistake shows
  # up as a connection failure rather than as a silent pass.
  local api_password worker_password backup_password
  api_password="kip-e2e-api-$$"
  worker_password="kip-e2e-worker-$$"
  backup_password="kip-e2e-backup-$$"

  # A second corpus that exists ONLY in workspace beta. Workspace isolation is
  # not observable while both workspaces hold the same documents.
  local beta_token="ZETAMARKERTOKEN"
  mkdir -p "$work/beta-data"
  printf 'beta workspace only: %s\n' "$beta_token" > "$work/beta-data/beta-only.txt"

  # A lexical copy of the shipped profile plus that second source. Semantic
  # search stays off so this check never reaches a model runtime - including
  # one that belongs to the machine it runs on - and the corpus stays
  # deterministic.
  "$E2E_PYTHON" - "$E2E_PROJECT_ROOT/config/kip.example.toml" "$work/kip.toml" "$work/beta-data" <<'CONFIG'
import sys
from pathlib import Path

source, destination, beta_root = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = source.read_text(encoding="utf-8")
if "semantic_enabled = true" not in text:
    raise SystemExit("the example config no longer enables semantic search")
text = text.replace("semantic_enabled = true", "semantic_enabled = false")
text += f"""
[[sources.filesystem]]
name = "e2e-beta"
root = "{beta_root}"
enabled = true
read_only = true
settle_seconds = 0
include_extensions = [".txt"]
exclude_globs = ["**/.DS_Store"]
acl_scope = "workspace:default"
classification = "internal"
"""
destination.write_text(text, encoding="utf-8")
CONFIG

  # Every request carries the ACL scope the sources declare, so the ONLY
  # difference between the two workspaces is workspace_id. If a cross-workspace
  # read still returns nothing, workspace isolation is what stopped it.
  kip_as() {
    # kip_as DATABASE_URL WORKSPACE ARGS...
    local database_url="$1" workspace="$2"; shift 2
    e2e_clean_env \
      KIP_SKIP_DOTENV=1 \
      KIP_CONFIG="$work/kip.toml" \
      KIP_DATABASE_URL="$database_url" \
      KIP_CAS_PATH="$work/cas" \
      KIP_ENV=test \
      "$E2E_PROJECT_ROOT/scripts/kip" \
      --workspace "$workspace" --acl-scopes workspace:default "$@"
  }
  psql_as() {
    # psql_as URL SQL
    # </dev/null: the client runs through `docker exec --interactive`, which
    # would otherwise drain the stdin of a `while read` loop around it.
    psql "$1" --no-psqlrc --set ON_ERROR_STOP=1 --tuples-only --no-align --command "$2" </dev/null
  }
  psql_scalar() {
    # psql_scalar URL SQL - the last output line, so a leading `SET` command
    # tag (psql prints one per statement) does not become the answer.
    psql_as "$1" "$2" | grep -v '^$' | tail -n 1
  }

  e2e_log "Migrating the throwaway database as the owner"
  kip_as "$owner" default migrate > "$work/migrate.json"

  e2e_log "Indexing two workspaces with different corpora"
  kip_as "$owner" alpha sync run --source sample > "$work/sync-alpha.json"
  kip_as "$owner" beta sync run --source e2e-beta > "$work/sync-beta.json"

  e2e_log "Applying deploy/sql/roles.sql.template after migrations"
  printf '%s\n' "$owner" > "$work/roles-url"
  chmod 600 "$work/roles-url"
  e2e_clean_env \
    KIP_ROLES_DATABASE_URL_FILE="$work/roles-url" \
    KIP_API_DB_PASSWORD="$api_password" \
    KIP_WORKER_DB_PASSWORD="$worker_password" \
    KIP_BACKUP_DB_PASSWORD="$backup_password" \
    "$E2E_PROJECT_ROOT/deploy/apply-roles.sh"

  e2e_log "Role attributes the API and worker must have"
  local attributes
  attributes="$(psql_as "$owner" \
    "SELECT rolname
       || CASE WHEN rolsuper THEN ' super' ELSE ' nosuper' END
       || CASE WHEN rolbypassrls THEN ' bypassrls' ELSE ' nobypassrls' END
     FROM pg_roles
     WHERE rolname IN ('kip_api','kip_worker','kip_backup','kip_reviewer')
     ORDER BY rolname")"
  printf '%s\n' "$attributes" > "$work/role-attributes.txt"
  local role
  for role in kip_api kip_worker kip_reviewer; do
    grep -qx "$role nosuper nobypassrls" "$work/role-attributes.txt" \
      || e2e_fail "$role must be NOSUPERUSER and NOBYPASSRLS; pg_roles says: $(grep "^$role " "$work/role-attributes.txt" || echo absent)"
    e2e_note "$role is not a superuser and does not bypass row level security"
  done
  grep -qx "kip_backup nosuper bypassrls" "$work/role-attributes.txt" \
    || e2e_fail "kip_backup must bypass row level security so a backup is not silently workspace-filtered"
  e2e_note "kip_backup bypasses row level security, as the backup path requires"

  local api_url worker_url backup_url
  api_url="${owner/kip_owner:test-password/kip_api:$api_password}"
  worker_url="${owner/kip_owner:test-password/kip_worker:$worker_password}"
  backup_url="${owner/kip_owner:test-password/kip_backup:$backup_password}"
  [[ "$api_url" != "$owner" ]] || e2e_fail "could not derive the kip_api connection URL"

  e2e_log "A cross-workspace read through the application, connected as kip_api"
  local alpha_hits beta_hits
  kip_as "$api_url" alpha search "$beta_token" --limit 10 > "$work/search-alpha.json"
  kip_as "$api_url" beta search "$beta_token" --limit 10 > "$work/search-beta.json"
  alpha_hits="$("$E2E_PYTHON" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["data"] or []))' "$work/search-alpha.json")"
  beta_hits="$("$E2E_PYTHON" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["data"] or []))' "$work/search-beta.json")"
  [[ "$beta_hits" != "0" ]] \
    || e2e_fail "workspace beta could not find its own document; the isolation check would pass vacuously"
  [[ "$alpha_hits" == "0" ]] \
    || e2e_fail "workspace alpha returned $alpha_hits hits for a document that exists only in workspace beta"
  e2e_note "kip_api: workspace beta finds $beta_hits hits, workspace alpha finds 0 for the same query"

  e2e_log "The same isolation in raw SQL, on every table that forces row level security"
  local visible
  # search.lexical_units carries the workspace-only policy, so one session GUC
  # is the whole predicate: rows visible there prove the kip_api session is not
  # simply seeing nothing, which would make every zero below vacuous.
  visible="$(psql_scalar "$api_url" "SET kip.workspace_id = 'alpha'; SELECT count(*) FROM search.lexical_units")"
  [[ "${visible//[[:space:]]/}" != "0" ]] \
    || e2e_fail "kip_api saw no rows at all in workspace alpha; the check would pass vacuously"
  e2e_note "kip_api sees $visible lexical units in workspace alpha"
  # Every table with FORCE ROW LEVEL SECURITY (what migration 0028 applies) and
  # a workspace_id column, read from the catalog so a table a later migration
  # adds is covered without editing this script. The owner is the bootstrap
  # superuser, so its count of beta rows is the truth the kip_api count is
  # compared against.
  psql_as "$owner" \
    "SELECT format('%I.%I', namespace.nspname, class.relname)
            || '|' || has_table_privilege('kip_api', class.oid, 'SELECT')
       FROM pg_class AS class
       JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace
      WHERE class.relkind IN ('r', 'p')
        AND class.relforcerowsecurity
        AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
        AND namespace.nspname NOT LIKE 'pg\\_%'
        AND EXISTS (SELECT 1 FROM pg_attribute AS attribute
                     WHERE attribute.attrelid = class.oid
                       AND attribute.attname = 'workspace_id'
                       AND NOT attribute.attisdropped)
      ORDER BY 1" > "$work/forced-tables.txt"
  local table granted forced=0 populated=0 ungranted=0 beta_rows leaked
  while IFS='|' read -r table granted; do
    [[ -n "$table" ]] || continue
    forced=$((forced + 1))
    if [[ "$granted" != true ]]; then
      # No SELECT grant: kip_api cannot read the table at all.
      ungranted=$((ungranted + 1))
      continue
    fi
    beta_rows="$(psql_scalar "$owner" "SELECT count(*) FROM $table WHERE workspace_id = 'beta'")"
    leaked="$(psql_scalar "$api_url" "SET kip.workspace_id = 'alpha'; SELECT count(*) FROM $table WHERE workspace_id = 'beta'")" \
      || e2e_fail "kip_api could not count $table in workspace alpha"
    [[ "${leaked//[[:space:]]/}" == "0" ]] \
      || e2e_fail "kip_api read $leaked of the $beta_rows workspace beta rows in $table while its session was workspace alpha"
    [[ "${beta_rows//[[:space:]]/}" == "0" ]] || populated=$((populated + 1))
  done < "$work/forced-tables.txt"
  (( forced > 0 )) || e2e_fail "no table forces row level security; migration 0028 is not in effect"
  (( populated > 0 )) || e2e_fail "no forced table holds a workspace beta row; every zero above would be vacuous"
  e2e_note "kip_api read 0 workspace beta rows from each of the $((forced - ungranted)) forced tables it can select ($populated of them hold beta rows; $ungranted more grant it no SELECT)"
  if psql_as "$api_url" "SET row_security = off; SELECT count(*) FROM search.lexical_units" >/dev/null 2>&1; then
    e2e_fail "kip_api was able to run with row_security = off; row level security is decorative"
  fi
  e2e_note "kip_api cannot disable row level security"
  if psql_as "$worker_url" "SELECT 1" >/dev/null 2>&1; then
    e2e_note "kip_worker can log in as its own role"
  else
    e2e_fail "kip_worker cannot log in; the worker would fall back to no credentials"
  fi

  e2e_log "A backup taken as kip_backup is non-empty and restores"
  local dump_bytes backup_dir
  e2e_clean_env \
    KIP_SKIP_DOTENV=1 \
    KIP_CONFIG="$work/kip.toml" \
    KIP_DATABASE_URL="$owner" \
    KIP_BACKUP_DATABASE_URL="$backup_url" \
    KIP_BACKUP_PATH="$work/backups" \
    KIP_CAS_PATH="$work/cas" \
    KIP_ENV=test \
    "$E2E_PROJECT_ROOT/scripts/backup.sh" > "$work/backup.txt"
  backup_dir="$(find "$work/backups" -maxdepth 1 -mindepth 1 -type d -name '2*' | sort | tail -n 1)"
  [[ -n "$backup_dir" ]] || e2e_fail "scripts/backup.sh produced no backup set under $work/backups"
  dump_bytes="$(wc -c < "$backup_dir/kip.dump" | tr -d '[:space:]')"
  # A kip_backup login without SELECT on every sequence used to stop pg_dump at
  # the first sequence and leave a zero-byte dump behind.
  (( dump_bytes > 4096 )) || e2e_fail "the backup dump is $dump_bytes bytes; a real dump of this corpus is larger"
  e2e_note "backup set $backup_dir holds a $dump_bytes byte dump"

  psql_as "$owner" "CREATE DATABASE kip_restore" > /dev/null
  restore_url="${owner%/kip}/kip_restore"
  e2e_clean_env \
    KIP_SKIP_DOTENV=1 \
    KIP_CONFIG="$work/kip.toml" \
    KIP_DATABASE_URL="$owner" \
    KIP_RESTORE_DATABASE_URL="$restore_url" \
    KIP_RESTORE_CAS_PATH="$work/restore-cas" \
    KIP_RESTORE_CONFIRM=YES \
    KIP_RESTORE_EVIDENCE_PATH="$work/restore-evidence" \
    KIP_ENV=test \
    "$E2E_PROJECT_ROOT/scripts/restore.sh" "$backup_dir" > "$work/restore.txt"
  # Canonical state, counted as the owner on both sides. The projections are
  # rebuildable and scripts/restore.sh rebuilds the lexical one, so comparing a
  # projection would compare a rebuild against an incremental build; content
  # units are what a restore has to bring back unchanged.
  local source_units restored_units
  source_units="$(psql_scalar "$owner" "SELECT count(*) FROM content.units WHERE workspace_id = 'alpha'")"
  restored_units="$(psql_scalar "$restore_url" "SELECT count(*) FROM content.units WHERE workspace_id = 'alpha'")"
  [[ "${source_units//[[:space:]]/}" != "0" ]] || e2e_fail "the source database holds no content units to restore"
  [[ "${restored_units//[[:space:]]/}" == "${source_units//[[:space:]]/}" ]] \
    || e2e_fail "the restored database holds $restored_units content units in workspace alpha, the source held $source_units"
  e2e_note "restored database holds the same $restored_units content units in workspace alpha"

  e2e_log "PASS (database mode): roles, row level security, backup and restore"
  e2e_note "not covered here: that the api and worker CONTAINERS use these roles, and that the"
  e2e_note "API can boot at all. Run --mode compose for those."
}

# ============================================================ compose mode
run_compose_mode() {
  local archive home postgres_port api_port readyz attempt
  archive="$work/dist/kip-$version.zip"
  e2e_build_package "$archive"
  local mirror="$work/releases"
  mkdir -p "$mirror/v$version"
  cp "$archive" "$archive.sha256" "$mirror/v$version/"

  home="$work/home"
  mkdir -p "$home"
  DEPLOYMENT="$work/deployment"
  e2e_log "Installing the candidate package (the Compose stack comes from it)"
  e2e_clean_env HOME="$home" SHELL=/bin/bash KIP_SEMANTIC=off \
    KIP_RELEASE_BASE_URL="file://$mirror" \
    bash "$E2E_PROJECT_ROOT/scripts/install.sh" "$DEPLOYMENT" \
      --version "$version" --no-bootstrap --no-shell-profile --bin-dir "$home/bin"

  # The Compose stack needs the deployment's .env, not its Python environment:
  # every credential in compose.yaml is interpolated from it. Generate it with
  # the deployment's own scripts/bootstrap_env.py, which is the shipped tool
  # that writes it and needs nothing but stdlib Python. The full bootstrap is
  # what the installer end-to-end job covers; repeating it here would add five
  # minutes and prove nothing this job is about.
  e2e_clean_env HOME="$home" "$E2E_PYTHON" "$DEPLOYMENT/scripts/bootstrap_env.py" "$DEPLOYMENT"
  [[ -f "$DEPLOYMENT/.env" ]] || e2e_fail "scripts/bootstrap_env.py wrote no .env"
  local credential
  for credential in POSTGRES_PASSWORD KIP_API_DB_PASSWORD KIP_WORKER_DB_PASSWORD KIP_BACKUP_DB_PASSWORD KIP_API_KEY; do
    grep -q "^$credential=." "$DEPLOYMENT/.env" \
      || e2e_fail "the generated .env carries no $credential; the Compose stack cannot interpolate it"
  done

  postgres_port="$(e2e_free_port)"
  api_port="$(e2e_free_port)"
  # Give the deployment this run's ports so nothing collides with a deployment
  # that already exists on this machine. Rewrite the loopback database URLs
  # too: bootstrap filled them at 5432, and the port guard refuses a mismatch.
  e2e_set_published_ports "$DEPLOYMENT/.env" "$postgres_port" "$api_port"
  e2e_set_dotenv "$DEPLOYMENT/.env" "KIP_SEMANTIC=off"

  # compose.yaml pins `name: kip`. COMPOSE_PROJECT_NAME takes precedence over
  # it, which is what keeps this run out of an existing kip project's
  # containers and volumes.
  COMPOSE_PROJECT="kip-e2e-$$"
  # Scrubbed like every other child: Compose prefers a shell variable over the
  # deployment's .env, so an exported KIP_API_PORT or KIP_NAS_PATH would bind
  # a live port or mount a real NAS into this throwaway stack.
  compose() {
    ( cd "$DEPLOYMENT" && e2e_clean_env COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" \
        docker compose -f compose.yaml -f deploy/compose.roles.yaml --profile app "$@" )
  }

  # The image build pulls base images and packages from Docker Hub, PyPI and
  # npm. Retry it here, where it is idempotent, so a transient registry error
  # does not fail the release; app-up.sh below then builds from that cache.
  e2e_log "Pulling and building the stack's images"
  # compose.yaml pins PostgreSQL by digest. When that exact image is already
  # here, pulling only asks the registry to confirm it, and a stalled registry
  # turned that into a silent twelve-minute hang; skip it. Without --quiet a
  # slow pull that does run shows its progress in the job log.
  if docker image inspect "$(e2e_postgres_image)" >/dev/null 2>&1; then
    e2e_note "the pinned PostgreSQL image is already present; not pulling it"
  else
    e2e_retry 3 "docker compose pull postgres" compose pull postgres \
      || e2e_fail "could not pull the PostgreSQL image"
  fi
  e2e_retry 3 "docker compose build" compose build \
    || e2e_fail "could not build the runtime image"

  e2e_log "Starting the Compose application profile through the deployment's app-up.sh"
  ( cd "$DEPLOYMENT" && e2e_clean_env HOME="$home" SHELL=/bin/bash \
      ${E2E_DOCKER_CONFIG[@]+"${E2E_DOCKER_CONFIG[@]}"} \
      COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT" KIP_SEMANTIC=off \
      ./scripts/app-up.sh )

  e2e_log "The API answers /readyz"
  readyz=""
  attempt=0
  until readyz="$(curl -fsS --max-time 5 "http://127.0.0.1:$api_port/readyz" 2>/dev/null)"; do
    attempt=$((attempt + 1))
    if (( attempt >= 60 )); then
      compose logs api >&2 || true
      e2e_fail "the API never answered http://127.0.0.1:$api_port/readyz. An identity mode the code
  rejects, or any other invalid container configuration, stops the API here."
    fi
    sleep 2
  done
  e2e_note "readyz: $readyz"

  e2e_log "The api and worker containers connect as the non-superuser roles"
  # The pool opens with min_size=0, so a worker session exists only after its
  # first poll: retry for about 30 seconds before calling a session absent.
  attempt=0
  while :; do
    compose exec -T postgres psql \
      --username kip_owner --dbname kip --no-psqlrc --tuples-only --no-align \
      --command "SELECT DISTINCT activity.usename || CASE WHEN role.rolsuper THEN ' super' ELSE ' nosuper' END
                   FROM pg_stat_activity AS activity
                   JOIN pg_roles AS role ON role.rolname = activity.usename
                   WHERE activity.datname = 'kip'
                   ORDER BY 1" > "$work/backends.txt" 2>/dev/null || true
    if grep -qx 'kip_api nosuper' "$work/backends.txt" && grep -qx 'kip_worker nosuper' "$work/backends.txt"; then
      break
    fi
    attempt=$((attempt + 1))
    (( attempt < 15 )) || break
    sleep 2
  done
  e2e_note "database backends: $(tr '\n' '; ' < "$work/backends.txt")"
  grep -qx 'kip_api nosuper' "$work/backends.txt" \
    || e2e_fail "no non-superuser kip_api session is connected; the API is not using the role 3.13.0 introduced"
  grep -qx 'kip_worker nosuper' "$work/backends.txt" \
    || e2e_fail "no non-superuser kip_worker session is connected; the worker is not using the role 3.13.0 introduced"
  # The owner may still appear: migrate runs as the owner and psql above
  # connects as it. What must not appear is a superuser session that belongs
  # to the api or the worker, which the two assertions above already exclude.

  e2e_log "PASS (compose mode): the stack boots and the API and worker are not superusers"
}

# ================================================= production-config mode
run_production_config_mode() {
  local python="$E2E_PROJECT_ROOT/.venv/bin/python" secrets="$work/secrets" rendered="$work/production.json" name
  [[ -x "$python" ]] \
    || e2e_fail "this mode imports kip from this checkout and needs its locked environment: $python is missing (run ./scripts/bootstrap.sh)"
  mkdir -p "$secrets" "$work/nas" "$work/ontology"
  for name in postgres_password migrate_database_url api_database_url worker_database_url; do
    printf 'placeholder\n' > "$secrets/$name"
    chmod 600 "$secrets/$name"
  done

  e2e_log "Rendering compose.production.yaml with docker compose config"
  # Placeholders for every value the file requires. The identity mode is NOT
  # among them: it is whatever compose.production.yaml itself sets.
  ( cd "$E2E_PROJECT_ROOT" && e2e_clean_env \
      COMPOSE_PROJECT_NAME="kip-e2e-render-$$" \
      KIP_MIGRATE_IMAGE=local/kip-e2e-render:placeholder \
      KIP_API_IMAGE=local/kip-e2e-render:placeholder \
      KIP_WORKER_IMAGE=local/kip-e2e-render:placeholder \
      KIP_JWT_ISSUER=https://issuer.e2e.invalid/ \
      KIP_JWT_AUDIENCE=kip-e2e \
      KIP_JWT_JWKS_URL=https://issuer.e2e.invalid/.well-known/jwks.json \
      KIP_NAS_PATH="$work/nas" \
      KIP_ONTOLOGY_PATH="$work/ontology" \
      KIP_POSTGRES_PASSWORD_FILE="$secrets/postgres_password" \
      KIP_MIGRATE_DATABASE_URL_FILE="$secrets/migrate_database_url" \
      KIP_API_DATABASE_URL_FILE="$secrets/api_database_url" \
      KIP_WORKER_DATABASE_URL_FILE="$secrets/worker_database_url" \
      docker compose -f compose.production.yaml --profile migration config --format json ) > "$rendered" \
    || e2e_fail "docker compose could not render compose.production.yaml"

  e2e_log "Constructing the identity the api, worker and migrate containers build at start-up"
  e2e_clean_env PYTHONPATH="$E2E_PROJECT_ROOT/src" \
    "$python" "$E2E_PROJECT_ROOT/tests/e2e/production_identity.py" "$rendered" \
      --project-root "$E2E_PROJECT_ROOT" --service api --service worker --service migrate \
      --control-service api \
    || e2e_fail "compose.production.yaml configures a service whose identity the code does not construct"

  e2e_log "PASS (production-config): compose.production.yaml's api, worker and migrate construct their identity"
  e2e_note "not covered here: booting compose.production.yaml (images, secrets, JWKS, /readyz)."
}

case "$mode" in
  database) run_database_mode ;;
  production-config) run_production_config_mode ;;
  compose) run_compose_mode ;;
esac
