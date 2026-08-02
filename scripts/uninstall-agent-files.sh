#!/usr/bin/env bash
set -euo pipefail
mode="${1:-personal}"
case "$mode" in
  personal) target="$HOME/.claude/skills/knowledge-fabric" ;;
  project) target="${2:-$PWD}/.claude/skills/knowledge-fabric" ;;
  *) printf 'Usage: %s [personal|project [target-project]]\n' "$0" >&2; exit 2 ;;
esac
rm -rf "$target"
printf 'Removed %s\n' "$target"
