#!/usr/bin/env bash
set -euo pipefail
mode="${1:-personal}"
case "$mode" in
  personal) skills_root="$HOME/.claude/skills" ;;
  project) skills_root="${2:-$PWD}/.claude/skills" ;;
  *) printf 'Usage: %s [personal|project [target-project]]\n' "$0" >&2; exit 2 ;;
esac
for skill in knowledge-fabric kip-setup; do
  target="$skills_root/$skill"
  rm -rf "$target"
  printf 'Removed %s\n' "$target"
done
