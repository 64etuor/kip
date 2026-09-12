# ADR-055: Focused agent instructions and reliable local entry points

- **Status:** Accepted
- **Date:** 2026-09-10

## Context

The root instructions required ten documents before any task. Retrieval skills
duplicated CLI help, architecture rules, and optional curation workflows. This
increased context cost and let stale copies contradict implemented discovery
releases. The bridge could silently ignore an invalid explicit runtime path;
installation removed existing skills before copying; verification could report
success while lint, types, and dependency auditing were skipped.

## Decision

Keep task routing and non-obvious operational constraints in `AGENTS.md`.
Keep retrieval's essential evidence and consent boundaries in the portable
skill, with conditional references for maintenance, curation, and interaction.
Use CLI help and generated contracts for arguments, canonical docs for design,
and implementation/status evidence for readiness. Do not duplicate those
catalogs in the always-loaded instructions. Preserve the Claude import and
byte-identical portable skill mirror.

An explicit runtime selection fails closed. Skill installation stages both
bundles, serializes installers targeting the same skill directory, and restores
old trees on handled failures before the final atomic pointer replacement.
The full verification gate preflights required Python tools and fails if any
are missing. A pip-only bootstrap remains supported using project modules;
uv verification uses the frozen lock.

## Consequences

No retrieval, approval, ACL, extraction, or public envelope contract changes.
The instruction reduction does not authorize model promotion, source mutation,
or maintenance during ordinary questions. Installation is recoverable for
handled failures, not a crash-atomic transaction across directories: a machine
failure can leave a staging directory with the previous bundles for recovery.
Verification cannot turn absent tools into successful evidence. Private-corpus
and recipient-runtime acceptance remain separate from this release's tests.
