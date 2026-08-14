# ADR-038: Make the ontology curation loop reviewable end to end

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

An audit of the ontology curation loop found six structural defects. The
mining job digest ignored the approved entity set, so the required
mine -> approve entities -> mine again loop deduplicated the second run onto
the finished job and never executed. One invalid or duplicate mined proposal
failed the whole batch. Approval was irreversible: `superseded`/`revoked`
statuses existed as literals with no code path. The evidence-required
predicate set was hardcoded in both stores and diverged from
`ontology/core/predicates.yaml`, permitting evidence-free approval of
`responds_to` and `records_decision`. Candidate review required roughly three
extra tool calls per candidate and had no triage ordering. The public
`include_candidate_assertions` flag reached only telemetry, and MCP callers
could not observe mining job outcomes at all. Ontology symbols also carried
no Korean labels for a Korean-language corpus.

## Decision

1. Bind the mining job idempotency digest to a hash of the caller-visible
   approved entity set, so approving entities makes a re-mine a new job.
2. Replace whole-batch mining failure with per-proposal skip-and-report
   (`MinedProposalSkip` in the miner result and `OntologyMiningSummary`);
   stale evidence units are skipped fail-closed per unit. Batch-level
   contract breaches still fail closed. The summary is recorded onto the
   durable job payload (`result`, `kip.ontology-mining-result.v1`) through a
   new `JobStore.record_job_result` port method, and `JobRecord` exposes
   `last_error`; a new MCP `kip_jobs` tool reads the same jobs service.
3. Add reversible review transitions: an application-service
   `revoke_assertion` (required note, recorded actor/timestamp) exposed on
   CLI/REST/MCP, excluded from every approved-only surface; and
   `supersede_contradicted` on approval, which retires the candidate's
   recorded contradictions as `superseded` in the same transaction.
   Migration `0019` adds the revocation audit columns.
4. Derive the evidence-required predicate set from the loaded catalog
   (`review == "required"` or `risk == "high"`) at approval time; stores
   keep a fail-closed floor (`candidate.review_risk == "high"` plus a single
   shared fallback set) pinned to `predicates.yaml` by a contract test, and
   candidate `review_risk` is normalized from the catalog at proposal time.
5. Return `kip.assertion-candidate-listing.v1` from candidate listings:
   risk/confidence triage ordering, subject/object display names, Korean
   predicate labels, ACL-gated evidence snippets, filters, and a total.
6. Make `include_candidate_assertions` real on surfaces that carry an
   ontology context section: `OntologyAnswerContext.candidates` lists
   proposed candidates labeled `status="proposed"`, separated from approved
   edges and excluded from evidence and citation requirements.
7. Add `label_ko`/`description`/`description_ko` presentation metadata to
   ontology entity types and predicates, passed through the mining contract
   and candidate listings. Label metadata is non-semantic and does not bump
   the release version or require a migration manifest.

## Consequences

- The curation loop is executable without manual digest workarounds, and a
  single bad proposal no longer discards a mining batch; every skip is
  auditable from job status on all three edges.
- Approval mistakes are correctable with an audited, note-required
  revocation instead of database surgery, and contradiction resolution has
  an explicit reviewed path.
- Evidence enforcement can no longer drift from the ontology contract; a
  contract test fails CI if the fallback set and `predicates.yaml` diverge.
- `GET /v1/review/candidates` and `kip review list` now return a versioned
  listing object rather than a bare candidate array; the bundled Python SDK
  was updated accordingly.
- Candidates remain candidates everywhere: opt-in visibility never feeds
  citations, generation relations, or evidence requirements.
