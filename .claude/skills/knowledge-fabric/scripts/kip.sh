#!/usr/bin/env bash
set -euo pipefail

find_root() {
  if [[ -n "${KIP_PROJECT_DIR:-}" ]]; then
    if [[ ! -x "$KIP_PROJECT_DIR/scripts/kip" ]]; then
      printf 'KIP_PROJECT_DIR has no executable scripts/kip. Fix the explicit runtime path.\n' >&2
      return 2
    fi
    printf '%s\n' "$KIP_PROJECT_DIR"
    return
  fi
  local current="${CLAUDE_PROJECT_DIR:-$PWD}"
  while [[ "$current" != "/" ]]; do
    if [[ -x "$current/scripts/kip" && -f "$current/AGENTS.md" ]]; then
      printf '%s\n' "$current"
      return
    fi
    current="$(dirname "$current")"
  done
  local pointer="$HOME/.config/kip/project-root"
  if [[ -f "$pointer" ]]; then
    local configured
    configured="$(cat "$pointer")"
    if [[ -x "$configured/scripts/kip" ]]; then
      printf '%s\n' "$configured"
      return
    fi
  fi
  printf 'KIP project root not found. Set KIP_PROJECT_DIR.\n' >&2
  exit 2
}

ROOT="$(find_root)"
exec "$ROOT/scripts/kip" "$@"
