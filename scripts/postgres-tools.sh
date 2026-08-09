#!/usr/bin/env bash

postgres_dump() {
  local database_url="$1"
  local output="$2"
  shift 2
  local executable="${PG_DUMP:-pg_dump}"
  if command -v "$executable" >/dev/null 2>&1; then
    "$executable" --format=custom --file "$output" "$@" "$database_url"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker compose exec -T -e "PGOPTIONS=${PGOPTIONS:-}" postgres \
      pg_dump --format=custom "$@" "$database_url" > "$output"
    return
  fi
  printf '%s\n' "pg_dump is unavailable and no Compose PostgreSQL service was found" >&2
  return 127
}

postgres_query_file() {
  local database_url="$1"
  local sql_file="$2"
  local output="$3"
  local executable="${PSQL:-psql}"
  if command -v "$executable" >/dev/null 2>&1; then
    "$executable" \
      "$database_url" \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 \
      --tuples-only \
      --no-align \
      --file "$sql_file" \
      > "$output"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker compose exec -T -e "PGOPTIONS=${PGOPTIONS:-}" postgres psql \
      "$database_url" \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 \
      --tuples-only \
      --no-align \
      < "$sql_file" \
      > "$output"
    return
  fi
  printf '%s\n' "psql is unavailable and no Compose PostgreSQL service was found" >&2
  return 127
}

postgres_query() {
  local database_url="$1"
  local query="$2"
  local executable="${PSQL:-psql}"
  if command -v "$executable" >/dev/null 2>&1; then
    "$executable" \
      "$database_url" \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 \
      --tuples-only \
      --no-align \
      --command "$query"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker compose exec -T -e "PGOPTIONS=${PGOPTIONS:-}" postgres psql \
      "$database_url" \
      --no-psqlrc \
      --set ON_ERROR_STOP=1 \
      --tuples-only \
      --no-align \
      --command "$query"
    return
  fi
  printf '%s\n' "psql is unavailable and no Compose PostgreSQL service was found" >&2
  return 127
}

postgres_restore() {
  local database_url="$1"
  local dump="$2"
  shift 2
  local executable="${PG_RESTORE:-pg_restore}"
  if command -v "$executable" >/dev/null 2>&1; then
    "$executable" "$@" --dbname "$database_url" "$dump"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    docker compose exec -T -e "PGOPTIONS=${PGOPTIONS:-}" postgres \
      pg_restore "$@" --dbname "$database_url" < "$dump"
    return
  fi
  printf '%s\n' "pg_restore is unavailable and no Compose PostgreSQL service was found" >&2
  return 127
}
