# Production Search Completion Design

**Status:** Approved for implementation
**Date:** 2026-08-13
**Scope:** Retrieval contracts, evidence-bounded answers, semantic runtime, and production evaluation

## Decision

The current implementation becomes the production design baseline. The
PostgreSQL production profile includes pgvector and a 1024-dimensional HNSW
projection. Semantic retrieval remains a rebuildable, versioned projection and
is not active merely because the extension, model adapter, or index exists.

`SearchRequest` is the canonical retrieval contract. REST, CLI, MCP, and the
Python SDK expose its mode and filters without defining edge-specific search
semantics. Context and answer requests inherit the same retrieval controls.

The answer path keeps hard refusal rules narrow. It refuses when an explicit
identifier is absent from the evidence, a numeric question has no value-bearing
evidence, an otherwise generic question maps to multiple documents without a
subject, or evidence is stale/unauthorized. Ordinary paraphrase relevance is
left to lexical/vector retrieval and validated generated claims, rather than a
growing list of domain heuristics.

## Runtime and data flow

1. An edge parses a versioned `SearchRequest`, `ContextRequest`, or
   `AnswerRequest`.
2. ACL scopes are resolved once. Explicit CLI scopes replace ambient scopes;
   an explicitly empty scope set remains empty.
3. Retrieval selects lexical, vector, hybrid, or reranked mode from the request.
4. Vector search uses the configured embedding space and the HNSW projection.
5. Exact evidence is reopened before any answer. Stale evidence is excluded.
6. Deterministic answer-adequacy checks run before extractive output or model
   egress. Structured generation must still pass claim/citation validation.

## Capability semantics

`semantic_search=true` means all of the following are true for the requesting
workspace: semantic search is configured, an embedding adapter is available, a
compatible embedding space is active, and every current ACL-visible content
unit is represented by the active space. Configuration alone is not readiness.

## Evaluation

Production quality uses two independent gates:

- a committed 100+ case portable regression suite that runs on every CI build;
- the reviewed private-corpus evaluation, which measures lexical, vector,
  hybrid, reranked, ACL, latency, freshness, answer, and refusal behavior.

Portable regression protects behavior but cannot certify private-corpus
quality. Semantic activation requires the private report and a live sidecar.
No dataset is labeled reviewed merely because it was generated mechanically;
every promoted case carries reviewer and source-revision metadata.

## Operations and failure behavior

The Infinity sidecar stays on loopback with remote egress disabled. Startup,
model readiness, projection completeness, HNSW availability, and active-space
compatibility are separate observable checks. A missing model, stale projection,
or failed quality gate returns a typed unavailable/refusal result and never
silently activates semantic retrieval.

## Verification

The change is complete only after targeted red/green tests, PostgreSQL
integration tests, the 100+ case CI gate, the full `scripts/verify.sh` suite,
live CLI/API requests, and a live sidecar retrieval evaluation all pass. PRD,
TRD, contracts, operations, security, implementation status, and the alignment
register are updated in the same change.
