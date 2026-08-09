# ADR-029: Bind end-to-end RAG gates to reviewed immutable datasets

## Status

Accepted

## Context

Retrieval recall alone cannot establish that generated claims are supported or
that an ontology path is correct, current, internally consistent, and
ACL-safe. A partial annotation set can also look perfect when unreviewed cases
are silently excluded from an average. Model-authored judgments cannot serve
as canonical expected truth.

## Decision

KIP binds answer and ontology review bundles to a dataset name, version, and
source revision. Dataset and case lifecycle metadata distinguishes drafts from
reviewed, golden, challenge, and canary releases. Reports omit reviewer
identity but retain reproducibility fingerprints and lifecycle eligibility.

Required answer and ontology dimensions must cover every evaluated case.
Promotion treats missing reviews and `null` metrics as failures. It requires
perfect per-case claim, citation, refusal, entity, relation, evidence,
contradiction, path, and temporal scores, with zero unsupported claims,
duplicates, orphans, and ACL leaks. Retrieval improvement, latency, stale
source, failure, and regression gates remain mandatory.

Expected answers and graph facts remain reviewer-authored data. LLM judges may
add shadow observations but cannot mark a dataset reviewed or activate a
candidate.

## Consequences

- Historical retrieval-only reports remain readable but cannot promote a new
  retrieval candidate without immutable lifecycle metadata.
- Review bundles that drift from case expectations or source revisions fail
  before report creation.
- Operators must create a new dataset version when expected evidence changes;
  editing an already reviewed artifact invalidates its fingerprint.
- The bundled ontology starter proves the contract only and does not represent
  tenant production quality.
