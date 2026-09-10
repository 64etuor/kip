#!/usr/bin/env bash
# Shared Kordoc runtime layout. requirements/kordoc/package.json is the single
# source of truth: dependencies.kordoc is the exact version and the manifest's
# major version is the runtime revision (var/kordoc-<version>-r<revision>).
# Requires PROJECT_ROOT and node on PATH.

kordoc_manifest() {
  printf '%s\n' "$PROJECT_ROOT/requirements/kordoc/package.json"
}

kordoc_expected_version() {
  node -p 'require(process.argv[1]).dependencies.kordoc' "$(kordoc_manifest)"
}

kordoc_runtime_root() {
  local revision
  revision="$(node -p 'require(process.argv[1]).version.split(".")[0]' "$(kordoc_manifest)")"
  printf '%s/var/kordoc-%s-r%s\n' "$PROJECT_ROOT" "$(kordoc_expected_version)" "$revision"
}
