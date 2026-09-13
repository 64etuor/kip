#!/usr/bin/env bash
# Print the CHANGELOG.md section for one released version.
#
#   ./scripts/changelog-section.sh 3.14.1
#   ./scripts/changelog-section.sh 3.13.0 --file /path/to/CHANGELOG.md
#
# The publish workflow uses this to give every GitHub release its own notes
# instead of the same fixed text. It matches the heading on the VERSION FIELD
# ALONE (`## 3.14.1 - 2026-09-13` matches `3.14.1`), so nothing has to predict
# the release date, and stops at the next `## ` heading. Leading and trailing
# blank lines are trimmed.
#
# Exit 1 when the version has no section. That is deliberate: AGENTS.md and
# CONTRIBUTING.md require a CHANGELOG entry for every behaviour, contract,
# configuration or deployment change, so a tag without one is an incomplete
# release and must fail before anything is published, not after.
#
# A GitHub release body caps at 125,000 characters. The largest section in this
# repository is about 17 KB, so nothing is truncated today; the guard below
# fails early instead of letting GitHub reject or silently cut the body. If a
# section ever did exceed the limit, the fix is to keep the section itself
# short and move the detail into the documents it describes - not to truncate
# the notes, which would publish an incomplete record of what changed.
set -euo pipefail

MAX_SECTION_BYTES="${KIP_CHANGELOG_MAX_SECTION_BYTES:-100000}"

usage() {
  printf 'Usage: changelog-section.sh VERSION [--file CHANGELOG_PATH]\n' >&2
}

version=""
changelog=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) [[ $# -ge 2 ]] || { usage; exit 2; }; changelog="$2"; shift 2 ;;
    --file=*) changelog="${1#--file=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) usage; exit 2 ;;
    *) [[ -z "$version" ]] || { usage; exit 2; }; version="$1"; shift ;;
  esac
done
[[ -n "$version" ]] || { usage; exit 2; }
if [[ -z "$changelog" ]]; then
  changelog="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/CHANGELOG.md"
fi
[[ -f "$changelog" ]] || { printf 'changelog-section: no such file: %s\n' "$changelog" >&2; exit 2; }

section="$(
  awk -v wanted="$version" '
    /^## / {
      heading = substr($0, 4)
      sub(/[[:space:]].*$/, "", heading)
      if (inside) { exit }
      if (heading == wanted) { inside = 1 }
      next
    }
    inside { print }
  ' "$changelog" \
  | awk '
      # Trim leading and trailing blank lines without buffering the whole
      # section: hold blank lines back until a non-blank line follows.
      /^[[:space:]]*$/ { if (started) { pending = pending "\n" } ; next }
      { if (started && pending != "") { printf "%s", pending } ; pending = ""; started = 1; print }
    '
)"

if [[ -z "$section" ]]; then
  printf 'changelog-section: %s has no "## %s" section\n' "$changelog" "$version" >&2
  exit 1
fi
if (( ${#section} > MAX_SECTION_BYTES )); then
  printf 'changelog-section: the %s section is %s characters, over the %s character limit a GitHub release body can carry with the rest of the notes. Shorten the section and move the detail into the documents it describes.\n' \
    "$version" "${#section}" "$MAX_SECTION_BYTES" >&2
  exit 1
fi
printf '%s\n' "$section"
