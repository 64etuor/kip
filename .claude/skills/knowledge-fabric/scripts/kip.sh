#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  # An installed copy records the deployment it was installed from, so copies
  # installed from different deployments never resolve to each other.
  local record="$SKILL_DIR/.kip-skill-install"
  if [[ -f "$record" ]]; then
    local recorded
    recorded="$(sed -n 's/^deployment=//p' "$record" | head -n 1)"
    if [[ "$recorded" == /* && -x "$recorded/scripts/kip" ]]; then
      printf '%s\n' "$recorded"
      return
    fi
    printf 'This skill was installed from %s, which has no executable scripts/kip. Reinstall it from its deployment (scripts/install-agent-files.sh) or set KIP_PROJECT_DIR.\n' "${recorded:-an unknown deployment}" >&2
    return 2
  fi
  # Legacy fallback only: copies installed before install records existed
  # (KIP 3.15.0 and earlier) relied on this one global pointer, which the most
  # recent install overwrote. Current installs no longer write it.
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
