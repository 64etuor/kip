#!/usr/bin/env bash
# No Python or .env dependency: this must work before the first bootstrap.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
if [[ "${KIP_USE_MANAGED_RUNTIMES:-1}" == 1 && ! -L "$PROJECT_ROOT/var" && ! -L "$PROJECT_ROOT/var/runtime" && ! -L "$PROJECT_ROOT/var/runtime/bin" ]]; then
  export PATH="$PROJECT_ROOT/scripts:$PROJECT_ROOT/var/runtime/bin:$PATH"
else
  export PATH="$PROJECT_ROOT/scripts:$PATH"
fi
if ! command -v docker >/dev/null 2>&1; then
  for KIP_DOCKER_BIN in /Applications/Docker.app/Contents/Resources/bin "$HOME/Applications/Docker.app/Contents/Resources/bin" "$HOME/.docker/bin"; do
    if [[ -x "$KIP_DOCKER_BIN/docker" ]]; then
      export PATH="$KIP_DOCKER_BIN:$PATH"
      break
    fi
  done
  unset KIP_DOCKER_BIN
fi
