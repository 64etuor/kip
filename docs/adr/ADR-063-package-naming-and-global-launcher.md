# ADR-063: Package naming, global `kip` launcher and `kip update`

Status: Accepted — 2026-09-11

## Context

The distributable was called a "starter kit" since ADR-052. With the
one-command installer and in-place upgrades (ADR-062) it is simply the KIP
release package, and the name leaked into asset names, manifest files, schema
identifiers, scripts, modules and the adoption guide. Users also had to `cd`
into the deployment and call `./scripts/kip`, and there was no CLI verb for
updating.

## Decision

- Name: `kip-<version>.zip` + `.sha256`, `KIP-MANIFEST.json`,
  `kip.package-archive.v1`, `build-package.sh` / `verify-package.sh` /
  `kip-package`, `kip.package_archive*`, `scripts/upgrade_package.py`,
  bundle directory `package/`, `docs/DEPLOYMENT_GUIDE.md`.
- Compatibility for one transition: readers accept the legacy manifest name
  and schema identifier; the upgrader removes the legacy manifest file after a
  successful upgrade and restores it on rollback; the installer falls back to
  the legacy asset name for releases before 3.10.0; releases publish a
  legacy-format copy (`scripts/legacy_archive.py`: same payload, manifest
  under its former name and schema identifier) with its own sidecar because
  the 3.9.x upgrader rejects the renamed manifest. The installer applies an
  upgrade with the upgrader shipped inside the downloaded archive rather than
  the deployment's older copy, then calls the new tree's
  `upgrade.sh --finish` for bootstrap, migrate and doctor. The legacy copy can be dropped once no 3.9.x deployment is
  expected to upgrade directly.
- Launcher: the installer writes `~/.local/bin/kip` (or `--bin-dir`) that
  execs the deployment's `scripts/kip`, honouring `KIP_HOME` as an override.
  ADR-061 kept bootstrap from rewriting shell profiles; the installer now adds
  a marked `# >>> KIP >>> … # <<< KIP <<<` block exporting `KIP_HOME` and the
  launcher directory on `PATH`. The block is replaced idempotently, nothing
  else is edited, and `--no-shell-profile` opts out. Bootstrap itself still
  does not touch profiles.
- `kip update` and `kip version` run without a database. `update` delegates
  to `scripts/upgrade.sh` so the CLI and the wrapper cannot drift.

## Consequences

Schema `kip.package-archive.v1` is a rename, not a shape change; the manifest
contract keeps the same fields. Historical ADRs, plans and CHANGELOG entries
keep the old name. Profile editing is a deliberate exception to ADR-061's
"preserve shell profiles" for the installer only, bounded to one marked block.
