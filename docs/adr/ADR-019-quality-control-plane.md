# ADR-019: Evidence-first quality control plane

**Status:** Accepted

## Context

KIP can already swap parsers and model adapters, build shadow semantic spaces,
and compare retrieval variants. Informal configuration changes are not enough
for continuous improvement: they do not fully bind a result to a candidate
revision, corpus, dataset, configuration, and code state. Ontology evolution
also needs an executable distinction between additive changes and changes that
invalidate existing assertions.

## Decision

KIP uses file-backed, versioned experiment manifests and immutable evaluation
reports as the quality control plane. A manifest changes one attributable
parser, embedding, reranker, or retrieval component, pins all reproducibility
inputs, and declares fail-closed promotion thresholds. A recommendation is
read-only. Candidate activation always remains an explicit operator action and
uses the existing atomic projection/extraction mechanisms.

Reviewed production failures may extend golden, challenge, or canary datasets.
Unreviewed queries and model judgments are not training truth. Answer quality
is measured from structured reviewed claims, evidence, citations, and refusal
expectations; unmeasured dimensions remain null.

Ontology releases are compared as complete normalized catalogs. Changes are
classified as compatible, review-required, or breaking. Breaking releases
require an explicit migration manifest with valid source and target symbols.
Approved assertions are never rewritten in place; migration produces
target-version candidates with preserved evidence and review provenance.

## Consequences

- Library and model upgrades can be reproduced and rejected without changing
  production behavior.
- ACL leaks, failed cases, missing evidence metrics, category regressions,
  stale fingerprints, and latency violations block recommendation.
- Ontology meaning remains independent of PostgreSQL enums, graph labels, and
  model versions.
- Operators retain the final activation decision.
- Scheduling experiments, executing parser ensembles, and materializing
  migrated assertion candidates remain separate application workflows rather
  than hidden side effects of search or diff commands.
