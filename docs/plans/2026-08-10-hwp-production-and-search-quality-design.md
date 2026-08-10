# HWP production parser and search-quality design

## Status

Approved on 2026-08-10. The operator accepted the native-primary parser decision
and asked KIP to apply further search improvements only when real-corpus evidence
supports them.

## Problem

The development and example configurations select `hwp-hwpx-parser` before
command fallbacks, but the container configuration does not enable the native
parser. Existing successful source revisions are also skipped by incremental
sync, so 81 of the 86 active OneDrive HWP/HWPX extractions still come from the
legacy Kordoc path. Changing configuration alone cannot update those active
projections.

The application layer also names the concrete Korean n-gram analyzer directly.
That makes future tokenizer experiments harder to isolate and conflicts with
the repository rule that replaceable integrations stay behind ports.

## Considered approaches

### 1. Configuration-only correction

Enable the native parser in `kip.container.toml` and stop. This fixes new
containers but leaves existing Kordoc extractions active indefinitely. Rejected
because the observable search surface would not receive the measured parser
quality improvement.

### 2. Native primary plus explicit, recoverable re-extraction

Enable the bundled native parser in every reference runtime, make Kordoc an
explicit opt-in command fallback, and add an operator-only re-extraction path.
Each document is parsed before any database mutation. Activation inserts the new
extraction and units, swaps the lexical projection, and deactivates the previous
extraction in one transaction. Old extraction data remains available for audit
and rollback. This is the selected approach.

Search analysis moves behind a small port. Additional tokenizer, fuzzy-match,
BM25, semantic, and reranker candidates remain shadow-only until the same
fingerprinted corpus demonstrates a recall or ranking gain without ACL,
freshness, latency, licensing, or deployment regressions.

### 3. Kordoc chunks or a new search engine by default

Index Kordoc RAG chunks in parallel and replace PostgreSQL lexical search with a
new engine or extension. Rejected for the default starter profile. Kordoc chunks
produced about 13.4 times as many units, did not recover a native miss, and still
ranked below native extraction. PostgreSQL extensions such as PGroonga or
ParadeDB can remain future `RetrievalStore` adapters, but they add image,
migration, backup, and hosted-database constraints that current quality evidence
does not justify.

## Architecture

### Parser selection

`ParserRegistry` remains the composition root for parser adapters. The native
Python adapter is the primary HWP/HWPX parser. External commands remain inside
`src/kip/adapters/parsers/`; domain and application modules never import Kordoc,
Node, or parser SDK types.

Kordoc is disabled in starter defaults. Operators that install a pinned Kordoc
binary can enable it as a command fallback. Production must not download a
parser through `npx -y` during an indexing job.

### Re-extraction and activation

The application builds a candidate `DocumentPacket` from the current immutable
source revision and a selected parser. A shadow run returns aggregate evidence
without writing source or canonical state. An activation run requires an
explicit CLI flag and calls a repository port operation that:

1. verifies the packet belongs to the current workspace and revision;
2. inserts the new extraction and units;
3. removes the previous active extraction from the lexical projection;
4. inserts the candidate units into the lexical projection;
5. deactivates the previous extraction and activates the candidate; and
6. records an audit event.

All six database actions occur in one transaction. A parser failure or a failed
transaction leaves the previous active extraction and search projection intact.
Source files are opened read-only and never modified.

### Search-quality candidates

Text analysis is represented by a `TextAnalyzerPort`. The current n-gram
implementation remains the default reference adapter so behavior is preserved
while alternative analyzers can be compared without changing application code.

The first candidate comparison covers:

- RapidFuzz local lexical reranking for typo and phrase similarity;
- Kiwi morphology for Korean lemma and compound handling;
- approved ontology aliases for deterministic query expansion; and
- PostgreSQL extension-backed BM25 as a future retrieval adapter.

RapidFuzz is MIT licensed and deployable as a normal Python wheel. Kiwi carries
LGPLv3 and a model/runtime footprint. Database extensions change the production
PostgreSQL image. None is enabled merely because it installs successfully.
Promotion requires a real OneDrive A/B against the corrected native extraction,
with exact and sparse-query Recall@5, MRR, P95 latency, unit count, and zero ACL
leaks recorded separately.

Approved ontology aliases may expand queries only from active entities visible
to the caller. Candidate entities and unapproved assertions never influence
normal search.

## CLI surface

The operator workflow is explicit:

```bash
./scripts/kip parser reextract --source company-nas
./scripts/kip parser reextract --source company-nas --activate
```

The first command is read-only shadow parsing. The second performs recoverable,
per-artifact atomic activation. Both emit the existing versioned JSON envelope
and report scanned, eligible, parsed, activated, failed, and warning counts.

## Safety and failure handling

- ACL scopes and ACL snapshot IDs are copied from the current source contract
  before units enter any projection.
- Current source SHA-256 must equal the indexed revision before activation.
- Failed or below-threshold extraction never replaces the active extraction.
- A missing optional parser is a visible per-file failure, not silent success.
- Reports contain aggregate metrics and public IDs, not document bodies.
- Kordoc output remains untrusted boundary data and must be parsed into typed
  structures before any future chunks adapter can emit content units.

## Verification

The change is accepted only when:

- a contract test proves the container registry is native-primary;
- repository contract tests prove failed activation preserves the old active
  extraction and successful activation exposes only the new units to search;
- CLI tests prove shadow mode is read-only and activation is explicit;
- dependency-boundary tests prove application/domain code imports no concrete
  analyzer or parser implementation;
- the full verification script passes;
- a real OneDrive shadow run parses all eligible HWP/HWPX files without changing
  source hashes; and
- the activated corpus reproduces or improves the prior native retrieval
  baseline before any additional search candidate is enabled.
