#!/usr/bin/env bash
# Apply deploy/sql/roles.sql.template as the database object owner, after
# migrations, and optionally give the group roles a password so a deployment
# without an external identity provider can log in as them.
#
# Connection, in order of precedence:
#   KIP_ROLES_DATABASE_URL_FILE  absolute path to a single-line connection URI
#                                (the production Compose secret-file convention)
#   standard libpq variables     PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE
# Use the owner credentials: the template needs CREATEROLE, and BYPASSRLS on
# kip_backup needs a superuser.
#
# Optional passwords, each applied only when set and non-empty:
#   KIP_API_DB_PASSWORD     -> ALTER ROLE kip_api LOGIN PASSWORD ...
#   KIP_WORKER_DB_PASSWORD  -> ALTER ROLE kip_worker LOGIN PASSWORD ...
#   KIP_BACKUP_DB_PASSWORD  -> ALTER ROLE kip_backup LOGIN PASSWORD ...
# A production deployment that binds its own login roles to the groups leaves
# all three unset; the groups then stay NOLOGIN.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/sql/roles.sql.template"
if [[ ! -f "$TEMPLATE" ]]; then
  printf 'apply-roles: missing %s\n' "$TEMPLATE" >&2
  exit 1
fi

CONNINFO=()
if [[ -n "${KIP_ROLES_DATABASE_URL_FILE:-}" ]]; then
  if [[ ! -f "$KIP_ROLES_DATABASE_URL_FILE" ]]; then
    printf 'apply-roles: KIP_ROLES_DATABASE_URL_FILE is not a readable file: %s\n' \
      "$KIP_ROLES_DATABASE_URL_FILE" >&2
    exit 1
  fi
  # Keep the URL out of the process list and out of the environment of the
  # psql children; only this shell holds it.
  CONNINFO=("$(head -n 1 "$KIP_ROLES_DATABASE_URL_FILE" | tr -d '\r\n')")
fi

run_psql() {
  psql --no-psqlrc --quiet -v ON_ERROR_STOP=1 "${CONNINFO[@]+"${CONNINFO[@]}"}" "$@"
}

# The Compose `roles` service starts after `migrate` completes, so PostgreSQL
# is already accepting connections. A manual run may race a restart.
attempt=0
until run_psql -c 'SELECT 1' >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if (( attempt >= 30 )); then
    printf 'apply-roles: cannot reach PostgreSQL as %s\n' "${PGUSER:-$(id -un)}" >&2
    run_psql -c 'SELECT 1' >/dev/null
    exit 1
  fi
  sleep 1
done

run_psql -f "$TEMPLATE"

set_login_password() {
  local role="$1"
  local password="$2"
  local escaped
  [[ -n "$password" ]] || return 0
  # The password goes in on stdin, never in argv: `psql -v password=...` would
  # publish the generated password in the process list of every process on the
  # host, which is the exposure this script already avoids for the connection
  # URI. The role name is not a secret, so it keeps psql's :"role" identifier
  # quoting. For the literal, standard_conforming_strings is set explicitly so
  # that doubling ' is the complete escape: with it on, a backslash in the
  # password is an ordinary character. printf writes the escaped value without
  # a further shell expansion pass, which a heredoc would perform.
  escaped="${password//\'/\'\'}"
  printf 'SET standard_conforming_strings = on;\nALTER ROLE :"role" LOGIN PASSWORD '\''%s'\'';\n' \
    "$escaped" | run_psql -v role="$role"
  printf 'apply-roles: %s can log in\n' "$role" >&2
}

set_login_password kip_api "${KIP_API_DB_PASSWORD:-}"
set_login_password kip_worker "${KIP_WORKER_DB_PASSWORD:-}"
set_login_password kip_backup "${KIP_BACKUP_DB_PASSWORD:-}"

if [[ -z "${KIP_API_DB_PASSWORD:-}${KIP_WORKER_DB_PASSWORD:-}${KIP_BACKUP_DB_PASSWORD:-}" ]]; then
  printf 'apply-roles: groups applied and left NOLOGIN; bind this deployment login roles with GRANT kip_api TO <login>\n' >&2
fi

printf 'apply-roles: applied %s\n' "$TEMPLATE" >&2
