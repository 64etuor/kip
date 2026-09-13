# ADR-067: Location-independent MCP registration and skill installs

- **Status:** Accepted
- **Date:** 2026-09-13
- **Amends:** ADR-055 (skill install transaction) and ADR-063 (global
  launcher): installs record their deployment per copy, and `kip mcp` is
  served through the launcher

## Context

A deployment is meant to answer agents working in any project, but both
integration paths assumed the agent started inside the deployment.

- Setup wrote the MCP entry as `bash scripts/mcp.sh` with a relative
  `KIP_CONFIG`. An MCP client starts the server from its own working
  directory, so the entry failed with `No such file or directory` in any other
  project and could not be registered at user scope. The server itself was
  location-independent; only the registration was not.
- Every skill install, personal or project, wrote one global pointer,
  `~/.config/kip/project-root`. A second deployment's install silently
  repointed the first deployment's project copies.
- Installed copies carried no version, and nothing refreshed them on upgrade.
  3.14.0 rewrote the skills to stop agents making unsupported freshness
  claims, so a stale copy is a correctness defect, not a cosmetic one.
- Uninstall removed any directory named `knowledge-fabric` or `kip-setup`
  without checking who installed it, and left the pointer behind.
- `kip setup` defaulted to the working directory even when started through
  the launcher.

## Decision

- **MCP registration.** Setup writes the deployment's absolute
  `scripts/mcp.sh` and `KIP_CONFIG`. `kip mcp` serves the stdio server with
  nothing but the protocol on stdout, so `kip mcp` through the global launcher
  is the location-independent entry for user-scope registration. Root options
  reach the server as the environment variables it already reads. An upgrade
  preserves `.mcp.json`: `kip doctor` reports a relative entry as the
  non-required `mcp_registration` check with the absolute replacement and
  never rewrites the file, and `setup verify` accepts both forms for this
  deployment's generated config.
- **Per-copy record.** Each installed skill directory carries
  `.kip-skill-install` (`kip.skill-install.v1`, key=value so the bash wrapper
  can read it): deployment root, `VERSION`, client, scope, time. The wrapper
  resolves `KIP_PROJECT_DIR`, then a checkout found by walking up, then its
  own record, and only then the legacy global pointer, kept for copies
  installed by 3.15.0 or earlier. A record naming a deployment that is gone
  stops with exit 2 instead of falling back. Installs no longer write the
  pointer.
- **Registry and refresh.** The deployment lists every skills directory it
  installed into in `var/skill-installs.json`
  (`kip.skill-install-registry.v1`), which upgrades preserve.
  `upgrade.sh --finish` reinstalls a registered location only when every
  skill directory there still carries this deployment's record; a removed
  location, another deployment's copy or an unrecorded same-named skill is
  skipped and reported, never created. A failed refresh warns and does not
  fail the upgrade. `kip doctor` reports stale copies as the non-required
  `skill_installs` check.
- **Legacy adoption.** The refresh adopts exactly one pre-record location: the
  personal Claude Code copy, when the legacy pointer names this deployment and
  both skill directories are real, unrecorded directories whose `SKILL.md`
  `name:` matches. Adoption keeps the legacy pointer, because project copies
  from 3.15.0 or earlier still resolve through it; they left no trace an
  upgrade can find and are refreshed only after a manual reinstall.
- **Clients.** `--client claude` (`.claude/skills`), `--client codex`
  (`.agents/skills`, the location Codex's "Build skills" documentation and its
  shipped loader both name) or `--client all`.
- **Boundaries.** Install refuses a destination inside the deployment itself:
  the deployment finds itself by walking up, and its `.claude/skills` must stay
  byte-identical to `skills/`. The comparison is by file identity, so a path
  that differs only in letter case on a case-insensitive filesystem is still
  the deployment. Uninstall removes only directories carrying this
  deployment's record and its registry entry. It never removes the legacy
  pointer, which record-less project copies still need; it prints how to
  remove it.
- **Setup.** `kip setup` defaults `--project-root` to the `KIP_PROJECT_ROOT`
  that `scripts/kip` exports, then the working directory.

## Consequences

- One deployment can serve any number of projects and both clients, and a
  second deployment no longer redirects the first one's copies.
- A fresh package install shows the `mcp_registration` warning until setup
  applies, because the shipped `.mcp.json` cannot know its install path; it
  still works when the client is opened in the deployment folder.
- An archive upgrade (`upgrade.sh --archive`) finishes with the function the
  old script already loaded, so an archive upgrade from 3.15.0 or earlier does
  not refresh or adopt. Run `./scripts/install-agent-files.sh --refresh` once
  after it. Download upgrades run the new tree's `upgrade.sh --finish`, and
  from 3.15.1 the archive path execs it as well instead of the loaded function.
- The registry lives in `var/`. A deployment restored without `var/` loses the
  list of install locations; its copies keep resolving through their own
  records but are not refreshed until reinstalled.
