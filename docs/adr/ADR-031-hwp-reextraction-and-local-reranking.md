# ADR-031: Guarded HWP re-extraction and local lexical reranking

Status: Accepted — reranker-default framing superseded by ADR-034 (2026-08-11)

> Amendment: RapidFuzz is no longer "the" production reranker; ADR-034
> promoted the candidate-local BM25 backend on an adversarially re-verified
> reviewed dataset, and RapidFuzz remains the fallback backend. The HWP
> re-extraction guards and the numeric rerank bounds in this ADR remain
> valid for the fallback path. Re-test trigger: re-compare fallback tuning
> when the corpus or the default backend changes materially.

## Context

Selecting a better parser in configuration does not update unchanged source
revisions that already have an active extraction. A normal sync must remain
incremental, while a failed candidate must never replace usable evidence.

The same OneDrive corpus also needed a low-cost search improvement that did not
weaken ACL filtering or force semantic infrastructure into the starter profile.
Kordoc, Kiwi, RapidFuzz, deterministic proximity, ontology alias expansion,
ParadeDB, and PGroonga were considered at their proper adapter boundaries.

## Decision

1. `parser reextract --source SOURCE` asks the filesystem port to scan and hash
   only HWP/HWPX candidates, then prepares them in shadow mode. `--activate` is
   a separate operator action.
2. Candidates inherit the current canonical source ACL snapshot, scopes, and
   classification through `EvidenceStore`; parser-only work never applies a
   configuration policy change. PostgreSQL activation verifies that policy
   together with the current revision, source hash, artifact, document, and
   quality gate. One transaction inserts the new extraction and units, replaces
   only active lexical rows, deactivates the previous extraction, and records an
   audit event. A partial unique index permits one active extraction per
   artifact. Historical extraction packets remain canonical and recoverable.
3. `hwp-hwpx-parser` remains the reference primary. Kordoc stays an optional,
   preinstalled command adapter and is disabled by default. Runtime package
   download is not an indexing strategy.
4. RapidFuzz 3.14.5 implements `RerankerPort` under `src/kip/adapters/`. It
   reranks at most 40 repository candidates after ACL/freshness filtering,
   reads at most 8,000 characters per candidate, blends 15% of baseline rank,
   and performs no embedding or network egress.
5. Application, domain, CLI, REST, and MCP code do not import RapidFuzz or any
   parser vendor SDK. All surfaces continue to use the same application
   services and ports.

## Evidence

The 2026-08-10 isolated PostgreSQL run scanned 86 real OneDrive HWP/HWPX files,
parsed and indexed 86, failed 0, and produced 263 native units. Source hashes
were identical before and after. The query set contained 253 source-derived
cases: 85 exact filenames, 85 one-character-deletion variants, and 83 sparse
source-term cases.

| Variant | Recall@1 | Recall@5 | MRR | P95 ms |
|---|---:|---:|---:|---:|
| PostgreSQL native lexical | 0.9407 | 0.9881 | 0.9596 | 119.359 |
| RapidFuzz over top 40 | 0.9684 | 0.9960 | 0.9796 | 134.431 |

RapidFuzz improved Recall@1 by 0.0277 and MRR by 0.0200 while adding 15.072 ms
at P95. The machine-readable decision is
`evaluation/reports/onedrive-hwp-native-rapidfuzz-20260810/decision.json`.

The earlier parser comparison remains part of ADR-017. Kordoc produced about
13.4 times the native unit count without recovering a native miss, and the
same-service sample scored below the native parser. It remains useful only as
an explicitly evaluated fallback for a corpus on which it recovers failures.

## Rejected defaults

- KiwiPiePy 0.23.2 reached Recall@1 0.9368, Recall@5 0.9921, and MRR 0.9605.
  Its lower top-1 and negligible aggregate gain did not justify another
  analyzer runtime, full lexical rebuild, or license surface.
- Deterministic proximity produced the same aggregate ranking as baseline.
- RapidFuzz plus Kiwi improved MRR only 0.0013 over RapidFuzz alone.
- Approved ontology alias expansion could not be evaluated because the
  workspace snapshot had no approved entities. Candidate assertions are never
  promoted to search facts to manufacture a gain.
- ParadeDB and PGroonga were not introduced without corpus evidence. Both add
  a PostgreSQL extension and deployment/upgrade surface; PostgreSQL-native FTS
  and `pg_trgm` remain the rebuildable baseline.

## Consequences and limits

- Re-extraction is intentionally HWP/HWPX-specific. There is still no generic
  destructive full-reindex shortcut.
- RapidFuzz degrades only to the already authorized lexical order and marks
  results with `lexical_rerank_degraded`; it cannot recover a document missing
  from the lexical top-40 candidate set.
- The A/B queries are source-derived, not human-reviewed natural-language
  questions. It does not establish answer faithfulness, citation quality, or
  ontology path relevance. Production adoption still needs reviewed private
  cases and ACL negatives.
- A different corpus may enable Kordoc, replace the analyzer, or adopt a search
  extension only through a new shadow report and explicit operator decision.
