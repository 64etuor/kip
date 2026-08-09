# ADR-021: Canonical classification and atomic model egress

- Status: Accepted
- Date: 2026-08-09

## Context

A global remote-egress boolean cannot decide whether a particular evidence unit
may be sent to a model. Taking a classification from an answer request or
connector payload would let an untrusted caller downgrade restricted content.
Filtering only disallowed units could also change an answer while making it
appear complete.

## Decision

Approved source configuration assigns `public`, `internal`, `confidential`,
`restricted`, or `personal` classification. Ingestion copies that value to the
canonical source object and every content unit, validates equality, and updates
unchanged revisions when source policy changes. PostgreSQL is canonical;
migration `0008_data_classification.sql` backfills unknown existing data as
`restricted`.

One application egress policy evaluates reopened canonical evidence. Local
generation is restricted to loopback endpoints. OpenAI and Anthropic require the
global remote switch, an explicit classification allowlist, a secret reference,
and a retention declaration. Non-public evidence requires `zero_retention`.
The decision returns admitted and denied evidence IDs and a stable redacted
policy fingerprint. Any denied member makes the batch ineligible for generation.

## Consequences

- Connector payloads and answer callers cannot lower a source classification.
- Reclassifying a source is an approved configuration change and takes effect on
  the next sync even when source bytes are unchanged.
- Existing rows remain safely restricted until their source policy is refreshed.
- Operators must verify provider retention terms outside KIP; configuration
  records their attestation and fails closed when it is absent.
- Future classification schema changes require an append-only migration and
  regenerated public contracts.
