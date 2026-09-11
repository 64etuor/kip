# ADR-062: One-command installation and manifest-driven in-place upgrades

Status: Accepted — 2026-09-11

## Context

Adopters installed KIP by downloading a starter ZIP, checking its sidecar by
hand, unzipping and running bootstrap. Upgrading an existing deployment was a
documented manual procedure (STARTER_KIT_GUIDE section 11) that unpacked the
new kit beside the old one and moved deployment-owned paths across. Both were
error-prone and there was no `kip upgrade` equivalent.

## Decision

- Publish `scripts/install.sh` as a release asset. It is standalone Bash with
  no Python, uv or project-code dependency, resolves the latest release (or a
  pinned `--version`), downloads the versioned starter kit and its `.sha256`
  sidecar, and refuses to extract unless the digest matches. A fresh install
  requires an empty directory and ends with `./scripts/bootstrap.sh`; the
  archive is then fully verified with `kip-starter-kit verify`.
- When the target already holds a kit-based deployment (`VERSION` and
  `STARTER-KIT-MANIFEST.json`), the installer hands the verified archive to
  that deployment's `scripts/upgrade.sh`. Git checkouts are refused; they
  update with `git pull`.
- `scripts/upgrade_kit.py` (standard library only) derives the boundary from
  the manifests: paths in the installed or new manifest are kit-owned and are
  replaced or removed; all other paths are deployment-owned and never touched.
  `.mcp.json` is the one kit-listed file setup also generates, so it is
  preserved. New files are staged beside their destination and renamed; the
  previous kit files and the plan are archived under `var/upgrades/<id>/`,
  which `--rollback` restores. Downgrades and archives whose internal digests
  or manifest disagree are refused. Bootstrap, migrate and doctor follow.

## Consequences

`curl | bash` trusts GitHub's release hosting and TLS; the checksum sidecar is
served from the same origin, so it protects against corruption and mismatched
assets rather than a compromised origin. Policies that require review can
download `install.sh`, read it, and pin `--version`; the manual path stays
documented. Upgrades keep Section 11's boundary but make it mechanical; the
deployment's PostgreSQL data is migrated append-only and is not part of the
file rollback, so a rollback across a migration boundary still needs the
database backup taken beforehand. Deployments created before 3.9.0 lack
`scripts/upgrade.sh`; they follow the manual procedure once, after which the
installer can upgrade them.
