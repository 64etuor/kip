# RAG Quality Audit — 2026-08-06

This audit evaluates the live 18,496-unit PostgreSQL workspace, the public
36-case golden set, read-only real-file parser samples, and the executable
contracts for ACL, graph, ontology, and evaluation reporting.

## Decision

Keep lexical retrieval as the production default. Keep vector, hybrid, and
reranked retrieval in shadow mode.

Hybrid retrieval improved rank ordering on this run, but did not improve
Recall@10 and cannot pass the stale-evidence gate because the golden set has no
stale expectations. Reranking produced the same measured quality as hybrid but
failed the two-second latency gate. No semantic space was activated.

## Live retrieval scorecard

Run: `eval_20260806T072914413857Z`

| Variant | Recall@10 | MRR | nDCG@10 | P95 ms | Failed cases | ACL leaks |
|---|---:|---:|---:|---:|---:|---:|
| lexical | 1.000 | 0.935 | 0.950 | 679.13 | 0 | 0 |
| vector | 1.000 | 0.972 | 0.979 | 756.02 | 0 | 0 |
| hybrid | 1.000 | 0.986 | 0.990 | 760.66 | 0 | 0 |
| reranked | 1.000 | 0.986 | 0.990 | 10029.13 | 0 | 0 |

All three comparisons returned `keep_disabled`. Vector and hybrid failed the
quality-improvement and stale-warning gates. Reranked also failed the latency
gate. The public set remains small and lexically distinctive, so these values
must not be generalized to a private production corpus.

The loaded-corpus lexical run met the two-second general lexical target, but
the sampled exact categories were above the 300 ms exact-identifier target.
Exact-numeric cases had the weakest lexical ordering, including two expected
documents at reciprocal rank 0.2. This is the next search-optimization target,
not a reason to activate an unproven semantic projection.

## Evaluation integrity improvements

- Unmeasured locator, latest-version, stale-warning, and zero-result-recovery
  metrics are now `null`, rather than synthetic 100% or 0% scores.
- Per-case execution failures are counted and block promotion.
- Semantic category deltas are averaged instead of selecting only the best
  category and hiding a regression.
- The activation gate treats an unmeasured stale-warning rate as a failure.
- Reproducibility fingerprints now include `uv.lock`.
- Markdown scorecards expose failed-case counts.
- Semantic projection activation now requires a promoted full report whose
  code and configuration fingerprints match the current runtime.

The current public golden set has no expected locator, latest-version, or
stale-warning fields. Those dimensions are therefore explicitly unmeasured.
Its ACL metric uses declared forbidden documents and does not prove protection
against every unlisted unauthorized document.

## Parser assessment

### HWP and HWPX

Three read-only real-file samples of each format passed the native parser. HWP
produced 1, 4, and 4 units; HWPX produced 2 units per sample. Every emitted unit
used an `hwp_structure` locator. Dispatch now verifies the OLE signature for
HWP and the required OPC entries for HWPX, rejecting extension-only false
positives.

The remaining limitation is material: the native adapter still emits bounded
text chunks with character offsets, not true section, paragraph, table, row,
column, footnote, or endnote locators. Also, the live active extraction set is
mostly from the previous Kordoc path (74 HWP and 7 HWPX), with only 5 native HWP
extractions. A shadow re-extraction and reviewed structural corpus are required
before calling FR-HWP-003/007 complete.

### XLSX

Three read-only real workbooks passed shallow parse and exact-range deep read.
Deep cells now expose formula/cached value separation, number format, date
semantics, and hidden row/column flags. Tests prove sparse-coordinate
preservation, source-byte immutability, and decompression-limit enforcement
before `openpyxl` expands the archive.

Numeric conclusions must still use `kip xlsx-read` against the current source;
the shallow index remains discovery-only by design.

## Embedding and reranking assessment

The configured Qwen3 1024-dimensional space remains `shadow`, with 476 vectors
for 18,496 current content units and no active space. This is only 2.6% workspace
coverage, although it includes the public evaluation documents. Vector and
hybrid search were exercised explicitly against the shadow space without
activating it.

The BGE reranker was exercised through the live local sidecar. On the loaded
workspace it did not improve the hybrid quality metrics and its 11.6-second P95
failed the latency gate. The pinned Jina trial also remains rejected by its
separate report because it reduced Recall@10. Both adapters stay optional.

## Graph and ontology assessment

PostgreSQL remains the canonical graph implementation. A critical ACL defect in
single-assertion reads was fixed: `get_assertion` now applies assertion scopes
before returning data, matching traversal behavior. The PostgreSQL integration
test exercises candidate approval, explanation, graph evidence, and a denied
restricted read. The live workspace has zero approved assertions, so production
graph relevance and depth-4 latency are not yet measurable.

Neo4j remains a port-only rebuildable projection. None of the adoption gates is
met, so deploying it would add operational risk without evidence of benefit.

Ontology YAML is now executable: verification checks entity inheritance,
predicate domain/range, explicit inverse, risk, extraction and review policies,
review-policy parity, semantic versions, and deterministic source relations.
Application candidate creation rejects unknown predicates and ontology
versions when the project ontology is present.

## Manual QA evidence

- `kip search` returned a versioned envelope and degraded safely to lexical
  because no semantic space is active.
- `kip read` reopened the exact PDF evidence unit with a `pdf_page` locator and
  `source_changed_since_index=false`.
- `kip xlsx-read` returned a fresh `A1:C3` range with the new semantic cell
  fields and matching indexed/current source hashes.
- `kip graph neighbors` returned a versioned empty result for an absent node;
  the non-empty assertion path is covered by the PostgreSQL integration test.

## Reproduction

```bash
./scripts/verify.sh
./scripts/kip projection status
./scripts/kip evaluate run \
  --dataset evaluation/golden/public-government.yaml \
  --variants lexical,vector,hybrid,reranked \
  --output-dir evaluation/reports/quality-audit-20260806-all \
  --warmup-passes 1
./scripts/kip evaluate compare \
  --report evaluation/reports/quality-audit-20260806-all/latest.json \
  --baseline lexical --candidate hybrid
```

The versioned report is under
`evaluation/reports/quality-audit-20260806-all/`. The evaluator report, parser
tests, ontology tests, and PostgreSQL integration test are the acceptance
evidence for this audit.

## Full OneDrive correction audit

The earlier parser assessment was sample-based and did not certify the complete
private corpus. A subsequent read-only shadow pass parsed all 1,681 eligible
OneDrive files with the current adapters and verified source hashes before and
after every completed parse.

| Format | Files | Parsed | Failed | Units | Files under 100 chars | Empty units | Warnings |
|---|---:|---:|---:|---:|---:|---:|---:|
| DOCX | 261 | 261 | 0 | 261 | 10 | 3 | 0 |
| HWP | 79 | 79 | 0 | 235 | 2 | 0 | 0 |
| HWPX | 7 | 7 | 0 | 28 | 0 | 0 | 0 |
| PDF | 691 | 684 | 7 | 27,374 | 94 | 1,437 | 1,925 |
| XLSM | 32 | 32 | 0 | 190 | 3 | 0 | 0 |
| XLSX | 611 | 610 | 1 | 3,343 | 34 | 0 | 3 |

No completed parse changed a source hash. Six failed `.pdf` files did not have
a PDF signature, one PDF was encrypted, and the failed `.xlsx` file was empty
and not a ZIP archive. PyMuPDF also reported one invalid embedded color profile.

The production sync was then expanded to include PDF and scanned all 1,681
files. It inserted 684 PDFs, retained 989 unchanged artifacts, and reported the
same eight failures. Of the active PDFs, 445 are complete and 239 are partial.
The 239 partial extractions contain all 1,925 low-text page warnings and have an
average text-coverage quality score of 0.511.

The PDF quality score previously stayed at `0.95` whenever any text existed,
even when most pages needed OCR. It now records page count, low-text page count,
text coverage, and scales quality by coverage. The HWP native locator also now
contains the ontology-required `section` key; it remains `null` until the
adapter can recover real structural sections.

A private exact-identifier coverage probe selected identifiers that occur in
only one OneDrive document. Across 154 cases, lexical Hit@5 was 154/154 and
Hit@1 was 153/154. This proves exact identifier coverage, not semantic question
answering. The private corpus still lacks reviewed paraphrase, locator,
revision, stale-source, graph-relation, and final-answer annotations.

The PostgreSQL integration scenario now exercises approved graph neighbors and
paths plus denial of both operations to a principal without the evidence ACL.
The default workspace still contains zero approved assertions, so real graph
relevance remains unmeasured. KIP also has no answer-generation adapter; its
answer rubric cannot provide end-to-end RAG certification until a generator
and reviewed answer set exist.
