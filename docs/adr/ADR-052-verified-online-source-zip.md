# ADR-052: Verified online source ZIP starter kit

- **Status:** Amended
- **Date:** 2026-08-17
- **Amended by:** ADR-062 (2026-09-11) — the manual procedure became the
  one-command installer and manifest-driven in-place upgrade
- **Amended by:** ADR-063 (2026-09-11) — the "starter kit" name was dropped
  and `docs/STARTER_KIT_GUIDE.md` was renamed `docs/DEPLOYMENT_GUIDE.md`

## Context

The production release bundle is intentionally broad: it carries a wheel,
image locks, SBOM, provenance, and an independent starter tree. The historical
source ZIP predates current contracts and cannot prove that its content matches
the current repository. An internet-connected recipient needs a smaller source
handoff that is safe to extract, independently verifiable, and sufficient to
bootstrap, test, and continue development.

## Decision

1. Ship a deterministic single-root ZIP named
   `kip-starter-kit-<version>.zip` from an explicit source allowlist.
2. Include implementation code, the frozen dependency lock, tests, contracts,
   migrations, ontology, examples, automation, and canonical operating docs.
   Exclude local configuration and state, internal plans, private evaluation
   data, databases, CAS/output data, caches, generated package metadata,
   repository metadata, and release binaries.
3. Embed a strict `kip.starter-archive.v1` manifest with per-file digests and
   source state. Add `SHA256SUMS` for the manifest and payload plus an external
   SHA-256 file for the exact ZIP bytes.
4. Verify allowlisted paths, required files, one root, regular unencrypted
   entries, bounded file count and expanded size, every digest, and selected
   private/secret patterns before accepting the archive.
5. Refuse dirty sources and existing output by default. `--allow-dirty` is an
   explicit local-candidate escape hatch recorded in the manifest.
6. Keep this source handoff separate from the signed production distribution.
   It carries no claim about an image, SBOM, provenance, or deployment approval.

## Consequences

- A recipient can verify the download before extraction and reproduce the
  locked environment over an internet connection.
- Adding a required source path or canonical document must update the archive
  allowlist and its contract tests in the same change.
- The archive scan is a bounded safety gate, not a general secret scanner or
  publisher-identity signature. Production releases keep their stronger gates.

## References

- `contracts/package-manifest.schema.json`
  (`contracts/starter-archive-manifest.schema.json` until 3.10.0)
- `docs/DEPLOYMENT_GUIDE.md` (`docs/STARTER_KIT_GUIDE.md` until 3.10.0)
- `docs/OPERATIONS.md`
- `docs/SECURITY.md`
