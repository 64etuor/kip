#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

mode="${1:-personal}"
case "$mode" in
  personal)
    skills_root="$HOME/.claude/skills"
    ;;
  project)
    skills_root="${2:-$PWD}/.claude/skills"
    ;;
  *)
    printf 'Usage: %s [personal|project [target-project]]\n' "$0" >&2
    exit 2
    ;;
esac
mkdir -p "$skills_root" "$HOME/.config/kip"
for skill in knowledge-fabric kip-setup; do
  target="$skills_root/$skill"
  rm -rf "$target"
  cp -R "$PROJECT_ROOT/skills/$skill" "$target"
  printf 'Installed %s Skill at %s\n' "$skill" "$target"
done
printf '%s\n' "$PROJECT_ROOT" > "$HOME/.config/kip/project-root"
printf 'Recorded KIP root at %s\n' "$HOME/.config/kip/project-root"
