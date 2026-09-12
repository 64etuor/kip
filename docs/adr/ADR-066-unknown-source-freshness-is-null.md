# ADR-066: Unknown source freshness is null, not "changed"

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

An empirical audit let an agent use a live deployment with only the shipped
instructions. It told the user that one of their legal documents had changed
after indexing. Nothing had changed. The source was a cloud placeholder holding
no local bytes, so `EvidenceUseCases.read_unit` computed
`source_changed_since_index` as `None != indexed_sha256`, which is `True`, and
reported `source_verification=unavailable` beside it. The documented rule —
"include the locator and stale-source status" — then turned a failed check into
a false factual claim about the user's document.

The same surface had three smaller gaps that pushed a caller the same way:
`xlsx-read` returned no `source_verification` at all, so a workbook value could
not be told apart from an unverified one; `is_latest` reads as cross-document
supersession although it is scoped to one logical document and defaults to
true; and `capabilities` reported its warnings only in `data.warnings` while
every instruction points at `meta.warnings`.

## Decision

- `source_changed_since_index` is three-valued on every producer that can fail
  to read a source: `true` (compared, differs), `false` (compared, matches),
  `null` (nothing was compared). `null` is always paired with
  `source_verification=unavailable`. `null` is chosen over a new typed value
  because `EvidenceRead` and `ContextItem` already declared the field nullable,
  so the versioned envelope and its generated schemas keep their shape; the
  meaning is carried by a schema description a generated client can see.
- `null` never reads as fresh. Every application-side consumer tests
  `is not False`, so an unverifiable unit is refused, not cited. The one
  truthiness test, in the answer evidence loop, is corrected.
- `XlsxRangeRead` reports `source_verification`, so the four reopen paths
  (`read`, `search` hits, `context` items, `xlsx-read`) all state how freshness
  was established. `xlsx-read` fails closed on an unreadable workbook rather
  than returning unverified cells, so its `source_changed_since_index` is never
  `null` and its `source_verification` is always `sha256`. The contract says so.
- `is_latest` keeps its SQL semantics. Because it lives in a free-form
  `SearchHit.metadata` map, the map's generated schema now describes it: same
  logical document only, defaulting to true when the document or its
  modification time is unknown.
- `capabilities` carries its warnings in `meta.warnings` as well as
  `data.warnings` on CLI, REST and MCP, so a caller checking the documented
  place sees them.
- A surface that relaxes or enumerates something must say so where the caller
  reads it: `allow_stale` states what it relaxes and what it still refuses in
  both the MCP schema and CLI help; the CLI names the `--mode` and
  `--direction` values the MCP schema enumerates, and says how the repeated
  `--source-kind`/`--document-type`/`--project-id` options differ in shape from
  the MCP array arguments; `kip --help` separates read-only retrieval from
  operator commands, in Rich panels and in prose for installations without
  Rich; and `csv_full_table_required` names `read`, which exists, instead of a
  CSV read tool, which does not.

## Consequences

Envelope versions are unchanged and the change is field-level, but it is not
purely additive for readers: a client that treated
`source_changed_since_index` as a plain boolean must stop reading `null` as
"unchanged". Integration notes in `APP_INTEGRATION.md` and the freshness
section of `DATA_CONTRACTS.md` carry the rule.

An unreadable source now produces a refusal path that says "unverified"
instead of "changed". That is less specific and equally safe: neither state
authorizes citing the unit, and neither authorizes a sync or re-index.

Grouped CLI help is presentation only. It does not enforce authorization; the
role checks on operator commands remain the enforcement point.
