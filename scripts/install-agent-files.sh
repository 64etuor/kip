#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

mode="${1:-personal}"
case "$mode" in
  personal)
    target="$HOME/.claude/skills/knowledge-fabric"
    ;;
  project)
    target="${2:-$PWD}/.claude/skills/knowledge-fabric"
    ;;
  *)
    printf 'Usage: %s [personal|project [target-project]]\n' "$0" >&2
    exit 2
    ;;
esac
mkdir -p "$(dirname "$target")" "$HOME/.config/kip"
rm -rf "$target"
cp -R "$PROJECT_ROOT/skills/knowledge-fabric" "$target"
printf '%s\n' "$PROJECT_ROOT" > "$HOME/.config/kip/project-root"
printf 'Installed knowledge-fabric Skill at %s\n' "$target"
printf 'Recorded KIP root at %s\n' "$HOME/.config/kip/project-root"
