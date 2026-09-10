# ADR-059: Explicit evidence state and reproducible agent startup

Status: Accepted — 2026-09-10

## Context

The independent 3.6.1 exercise found snippet-only additions in agent answers,
inconsistent PostgreSQL/Memory previews, an image-build dependency before a
CLI user could query, and npm advisories outside the Python dependency gate.
Filename discovery also succeeded where the answer's body-only relevance gate
refused to return that document's extracts.

## Decision

- Keep shared services and tool names. Search hits explicitly identify
  `evidence_role=discovery` and `source_verification=not_checked`. Reads,
  context items and citations report `stat`, `sha256`, or `unavailable`;
  context discloses per-item truncation. Stat reuse is not a new digest check.
- Generate one paragraph-bounded, query-aware discovery preview in both
  repository implementations after existing ranking/access checks. This is
  presentation, not content censorship or a replacement for exact evidence.
- Include common Korean particle stems in the existing ACL-scoped vocabulary
  abstention check. Unobserved inferred terms do not unlock retrieval.
- Treat an entire-query exact filesystem basename as a request for that
  document's extracts. Allowed files with that name but differing content are
  checked before the result limit and require clarification; identical copies
  are one document. Table completeness/freshness/access rules still apply.
  A filename embedded in a factual question scopes evidence to that document
  but does not bypass answer adequacy: the remaining question must be present
  in it, otherwise the answer refuses as not present. Naming several files is
  a comparison, not duplicate ambiguity; an exclusion marker directly after
  the name (`A.txt 말고`, `A.txt를 제외하고`) excludes that file, while the same
  words elsewhere are content; an embedded mention requires a file extension;
  approved ontology evidence is retained.
- Offer `app-up.sh --database-only` as the guided CLI/MCP path. Only the
  database credentials are required, readiness precedes migration, and external
  databases do not start an unrelated bundled service. Full app mode remains.
- Host and image installations share the exact npm manifest/lock and use
  `npm ci --omit=dev --ignore-scripts --no-audit`. Explicit audits fail on high
  findings or audit errors. CPU binaries/models are verified independently.

## Consequences

Additive response fields preserve the versioned envelope. Existing readers
must tolerate additional fields. The default context path still reuses matching
stat metadata; callers needing an independent digest use exact `read`.
The lexical changes do not enable semantic retrieval or prove general recall.
External model adherence still needs actual answer-level evaluation.

OCR now requires Node 20.9+ and uses the isolated r2 runtime. The moderate
adm-zip advisory remains in the graph; disabling lifecycle hooks removes its
identified ONNX install-time extraction path from the supported installation,
not the advisory itself. Custom GPU/source-build workflows are not validated by
the bundled CPU runtime. npm auditing requires registry access during setup,
verification and builds, never during normal retrieval.
