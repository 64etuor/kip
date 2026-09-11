# Production Design Alignment

- **Status:** Living conformance register
- **Last reviewed:** 2026-09-11 (3.12.0 semantic search on by default; older corpus measurements retain their dates)
- **Target:** `docs/PRD.md`, `docs/TRD.md`, and the approved
  [historical design](https://github.com/64etuor/kip/blob/1b04bad685762fe3002d9c4ec6a75f267df9fb94/docs/plans/2026-08-09-production-rag-package-design.md)
- **Current truth:** generated contracts, the current checkout, measured
  evaluation reports, and `docs/IMPLEMENTATION_STATUS.md`

This document prevents an approved target design from being mistaken for
implemented or production-accepted behavior. A target requirement remains a
gap until the current contract and runtime evidence satisfy it. Conversely, an
implementation change does not silently rewrite the target: it needs an ADR
and synchronized updates to the affected canonical documents.

## Document roles

| Document | Authority |
|---|---|
| `PRD.md` | Normative product requirements and release outcomes |
| `TRD.md` | Normative technical target and architecture invariants |
| `DATA_CONTRACTS.md` and generated schemas | Current public edge contracts |
| ADRs | Accepted design changes and their consequences |
| `IMPLEMENTATION_STATUS.md` | Current readiness and known limitations |
| This document | Target-to-current conformance and the ordered gap register |
| Dated plans and audits | Historical intent or evidence; never current status by themselves |

## Current alignment

3.12.0 makes semantic search the shipped default (ADR-065): the configured
default mode `hybrid` fuses lexical and vector candidates by reciprocal rank,
with the pinned Qwen3-Embedding-0.6B identity at a 4,000-character
`head_tail_v1` cap. `reranked` stays an explicit mode; BM25 keeps reranking the
lexical path, and the BGE cross-encoder is opt-in. Bootstrap installs the
hash-locked model runtime, sync and activated re-extraction keep the projection current,
and a complete release-reviewed space activates itself; other identities keep
the ADR-036/037 evaluation and explicit activation. Default-mode search
degrades to lexical with `semantic_degraded` (or, where a deployment sets
default mode `reranked`, to the fused ranking with `rerank_degraded`) and
reports it in `meta.warnings`. The lexical channel leaves query n-grams found
in at least 2% of lexical units out of candidate matching, which on the 19 reviewed private cases in lexical mode moved
Recall@10 from 78.9% to 89.5%, MRR from 58.3% to 63.8%, and P95 from 11.11 s to
2.22 s. The portable gate now runs the default mode as well as lexical, and the
private gate checks per-variant floors. Release evidence for the default mode:

Private reviewed set (19 cases, `onedrive-personal` 1,912 files / 176,545
units, space `qwen3-embedding-0.6b-1024-c4000-ht1`, float16 runtime on the
reference Mac, one warmup pass):

| Variant | Recall@10 | MRR | nDCG@10 | P50 | P95 |
|---|---:|---:|---:|---:|---:|
| lexical (BM25 rerank, common-term pruning) | 89.5% | 63.8% | 70.2% | 1.45 s | 2.22 s |
| vector | 89.5% | 83.3% | 84.9% | 0.06 s | 0.08 s |
| hybrid (default) | 89.5% | 89.5% | 89.5% | 1.64 s | 2.42 s |
| reranked, BM25 | 78.9% | 59.8% | 64.5% | 1.49 s | 2.33 s |
| reranked, BGE, 40 candidates | 89.5% | 83.3% | 84.9% | 6.87 s | 16.18 s |
| reranked, BGE, 20 candidates | 94.7% | 85.3% | 87.6% | 5.32 s | 7.20 s |

All variants had 0 failed cases and 0 unauthorized results. `hybrid` became
the default because BM25 reranking on top of the fused list fell below
lexical, while the BGE cross-encoder raised recall but not ranking quality at
3-4x the latency, so it stays opt-in. The private gate now records floors for
`hybrid` and the `lexical` fallback; the remaining limits are in
`IMPLEMENTATION_STATUS.md`.

3.8.0 adds prerequisite preparation before Python-dependent setup, with pinned
project-local runtimes, explicit system Docker installation, readiness checks
and external-DB Docker exemption. A real macOS cold runtime/full bootstrap was
exercised; Linux cold runtime smoke is included in CI. Native privileged Docker
installation on every OS is not established by simulated installer tests,
and first-run/permission/WSL choices remain explicit user actions (ADR-061).

3.7.1 corrects named-source binding before retrieval limits and live freshness
filtering, including quoted filenames and exclusions. Typed refusals preserve
the requested scope when evidence cannot be used. This is covered on both
Memory/PostgreSQL and the shared CLI/REST/MCP answer path; it does not establish
general natural-language or private-corpus answer quality (ADR-060).

The independent dispatched 3.6.1 follow-up in [agent quality](AGENT_QUALITY.md)
establishes core MCP task success without forced skill context, but not full
evidence discipline: two of five responses added claims without exact reads.
OneDrive scope checks passed on a two-file sample. Cold installation reached
host MCP retrieval using an isolated database, while full app startup stalled
on Dockerfile frontend resolution. Kordoc npm advisories were outside the
Python dependency gate.

3.7.0 addresses the startup and npm-gate parts of that follow-up:
`./scripts/app-up.sh --database-only` makes the database-only path the
supported CLI/MCP handoff instead of an improvisation, and the locked Kordoc
npm graph is now audited in installation, image build, CI, and `verify.sh`
(ADR-059). Response fields now state discovery versus verified evidence. The
evidence-discipline gap is not re-measured: no new answer-quality evaluation of
external generation models has been run, so that part of the follow-up remains
an open acceptance gap.

The follow-up 2026-09-10 [native agent exercise](AGENT_QUALITY.md) covers seven
synthetic requests, not broad production answer quality. MCP descriptions,
mutation hints and error envelopes are now tested; table completeness is
checked before answering. Superseded implementation checklists were removed,
and archive-aware Markdown link checks protect both document payloads.

The isolated 2026-09-10 setup acceptance completed frozen bootstrap, guided
plan/apply/verify, standalone Compose startup, migration, API/worker operation,
host-to-API and API-to-host ingestion/search/fresh-read parity, unauthenticated
401 rejection, REST smoke, and teardown. Source paths are identical across the
host and generated containers; this fixes the observed split-namespace failure
without relaxing source authorization. See `IMPLEMENTATION_STATUS.md` for the
bounded measurements and the unperformed private-corpus floor.

The 2026-09-10 changes close the current-root authorization and approved setup
handoff gaps (ADR-056/057). Scope checks apply before retrieval/graph processing
and survive existing indexes; generated Compose is standalone and uses the
approved identity, database, paths, and runtime owner. Shared host/container
source paths now preserve URI/snapshot identity. The OneDrive test passed
search/context/fresh exact read for all seven sample files and fresh workbook
reads for both spreadsheets, with removed/disabled/narrowed-scope denials and
repeat-sync idempotency. Its ingestion scope remains seven files/36 units, with
1,907 cloud-only files deferred from 1,914 eligible. It does not establish broad
production, model-quality, or all-recipient setup acceptance. Current measured
details and remaining acceptance limits belong in implementation status.

| Area | Production intent | Current implementation and evidence | Verdict |
|---|---|---|---|
| Canonical evidence and ingestion | PostgreSQL owns source identity, immutable revisions, ACL state, active extraction, and approved assertions; parser/projection failures never replace valid state | Implemented for pilot paths, including CAS capture, shadow extraction, guarded activation, current-revision reads, and disposable lexical/vector projections | Aligned for pilot; connector-specific failure semantics still need target-environment validation |
| Agent instruction scope | Load only task-relevant guidance while preserving evidence and consent boundaries | Compact root/skill entry points route to canonical docs and conditional references; explicit runtime errors and handled installation failures are covered by regression tests (ADR-055) | Aligned for local tooling; no new corpus-quality claim |
| Application boundaries | Focused use cases depend on capability ports; concrete adapters are selected only at composition | `Application` composes ingestion, retrieval, evidence, answer, knowledge, operations, telemetry, ontology, and interaction use cases. Architecture checks prohibit application-to-adapter imports | Aligned |
| Edge architecture and filters | CLI, REST, and MCP expose the same use-case semantics; the SDK consumes stable REST/OpenAPI contracts | `SearchRequest` mode and filters have CLI/REST/MCP/SDK parity and every edge calls the same retrieval service. The stdio edge uses MCP 2.0, reports the KIP version, and has current-protocol plus legacy-negotiation client coverage while retaining `kip.envelope.v1`. CLI envelopes retain typed errors although process exit codes are coarse; list/search edges remain limit-only rather than cursor-paginated | Aligned for search semantics; pagination and exit-code granularity remain |
| Agent-guided setup | Approved choices determine the effective runtime and missing prerequisites remain visible | Standalone generated Compose, approved host-config and secret selection, non-root owner-bound plans, folder shorthand, local/cloud preview, expanded secret readiness, and a database-first handoff are implemented (ADR-057). Receipts lead with `./scripts/app-up.sh --database-only`, which requires only the database credential and builds no API/worker image, and full `app-up.sh` remains for the API and worker (ADR-059). Explicit generation-gated candidate-mining consent and atomic apply remain | Handoff aligned; rerun recipient acceptance with real mounts, identity, DB, backup, egress, and any separately provisioned local generation service |
| Runtime topology | Digest-pinned non-root API, worker, migration, and PostgreSQL services; sources read-only; identity/TLS/secrets supplied by the deployment | `compose.production.yaml`, role templates, resource limits on all services, read-only containers, private database network, and loopback API binding exist. A digest-pinned `models` service (Infinity 0.0.77 CPU) serves the pinned embedding model read-only and offline on an internal network (the BGE reranker is opt-in), populated with both models once by the `models-fetch` profile, and PostgreSQL receives explicit memory settings (ADR-065). The API bind-mounts `${KIP_NAS_PATH}` read-only (evidence freshness and `xlsx-read` need the live source tree) and `${KIP_ONTOLOGY_PATH}` read-write (discovery auto-release, ADR-044; read-only on the worker), its healthcheck targets the database round-trip `/readyz`, and the worker has a database-connectivity healthcheck. It is a reference Compose deployment, not an orchestrator, TLS edge, or secret manager | Partial: hardened reference ready, environment control plane remains external |
| Connector coverage | Filesystem/NAS, Slack, and mail preserve stable IDs, revisions, cursors, tombstones, ACL snapshots, and outage safety | Filesystem is pilot-validated, including complete-scan deletion grace reconciliation with soft tombstone revisions and reappearance re-indexing (migration 0020). Directory walk errors fail closed, and files deferred by settle, symlink, filter, or size policy remain present for deletion accounting. Empty scans never contribute deletion evidence. The TRD's sentinel/count-drop guards and descriptor-pinned source reads remain unimplemented. Slack, Apple Mail, and IMAP are reference adapters requiring provider-specific scope, edit/delete, rate-limit, retention, permission, and UID validation | Partial by design |
| Parser, OCR, and exact evidence | Parsers emit reproducible locators through shadow activation, cannot exhaust the long-lived worker, OCR candidate evidence is locally available by default, and spreadsheet facts use exact original ranges | Every filesystem parser runs one document per bounded child behind the unchanged `ParserPort` (ADR-050). The starter PDF default is local `pdf_inspector` with Markdown-table promotion and selective exact PyMuPDF fallback (ADR-054): the original 1.14.2 A/B over six public PDFs preserved 70/70 pages and lexical Recall@10/MRR 1.0000/0.9861 with zero ACL leaks while raw parsing improved 19.2x and isolated sync 3.23x. The pin is now 1.19.0 (ADR-064): a separate migrated PostgreSQL A/B activated through `parser reextract --extension .pdf` kept 6/6 documents and 70/70 pages with 0 rejected/failed, raised table units from 37 to 45, recovered the garbled page natively (OCR units 13 to 0), and kept lexical Recall@10/MRR/nDCG@10, zero unauthorized results, and the top-1 (document, page) hit of 30/30 public cases identical after search text strips inline Markdown bold and presentation tags (`read` keeps the extractor Markdown). PDF and PPTX share pinned offline Kordoc 4.13.1 PP-OCRv5 Korean, whose `--format json --ocr` contract and synthetic scanned-PDF/table/PPTX OCR output matched 4.8.0 and whose real isolated sync produced searchable, bbox-located `pdf_ocr`/`pptx_ocr` units (ADR-053, ADR-064). Its npm graph is now locked and audited in installation, image build, CI, and `verify.sh`, with lifecycle scripts disabled and the unpatched moderate adm-zip advisory recorded in `SECURITY.md` (ADR-059). Native locators are preserved and XLSX exact reads remain bounded. Source read-only and network denial remain outer deployment controls | Partial overall: process containment, public PDF lexical parity, and scoped Kordoc compatibility pass, but semantic/reranked PDF parity was blocked by the absent model sidecar in both PDF A/Bs; private table accuracy, OCR CER/WER, deep spreadsheet semantics, placeholders, legacy Office/ODF/MSG/image parsers, PPTX media/OLE/legacy `.ppt`, full-corpus extraction, and broad retrieval quality remain unproven |
| Identity, ACL, and model egress | Trusted identity creates `RequestContext`; ACL/freshness filter before every retrieval; model transfer is classification-aware and fail-closed | JWT/API-key adapters, dynamic ACL snapshots, RLS, canonical classification, and atomic egress decisions exist. Repository/RLS probes found zero leaks. Local loopback models, and the compose `models` service named in `security.model_service_hosts`, are not remote egress | Aligned for pilot; recipient IdP/revocation and provider contract remain adoption gates |
| Active retrieval | Exact and lexical retrieval remain the baseline until a reviewed candidate passes all gates; since ADR-065 the release-reviewed semantic default is that candidate | Since 3.12.0 the default mode is `hybrid` (lexical and vector candidates fused by RRF) with lexical fallback; on the 19 reviewed private cases hybrid reached Recall@10/MRR `0.895/0.895`, while BM25 reranking of the fused list fell to `0.789/0.598`. The lexical path is the fixed `plan -> analyze -> abstain -> ranked pool -> diversify/truncate` pipeline with candidate-local BM25 and RapidFuzz fallback; common query n-grams (at least `search.lexical_common_term_fraction` of lexical units) are left out of candidate matching and the abstention gate is an existence check. On the 19 reviewed private cases in lexical mode Recall@10/MRR moved from `0.789/0.583` to `0.895/0.638` and P95 from 11.11 s to 2.22 s | Default changed under ADR-065; quality is not broad-rollout level |
| Retrieval regression gate | Regressions are blocked before promotion or merge | Hosted CI and `verify.sh` always run a deterministic 100-positive/20-ACL portable gate through `./scripts/golden-gate.sh`, for lexical and for the shipped default mode with a deterministic hashing embedding. The separate private gate checks per-variant floors (default mode and lexical fallback), fails when the corpus is indexed but configured semantic search is not ready, and fails closed when protected runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1`; a local missing-corpus skip is not private evidence | Aligned for portable merge protection; protected private runner remains a deployment gate |
| Pgvector production profile | The supported PostgreSQL reference profile carries a rebuildable vector projection and production ANN index while activation stays optional | Normal migrations install pgvector, the 1024d projection, and HNSW; vector queries use bounded strict iterative scan. EXPLAIN selected the HNSW index. `capabilities.semantic_search` requires an active space matching the configured identity; completeness (and a `stale` verdict) is counted by `kip doctor` and `kip projection verify`, and every sync keeps the projection current. Projection queries check ACL freshness and source policy once per artifact/snapshot, which kept them inside the statement timeout at about 176k units; projection statements use `database.projection_statement_timeout_ms` (300000) instead of the 15 s interactive timeout | Aligned under ADR-037 and ADR-065 (semantic search on by default; `KIP_SEMANTIC=off` keeps a lexical-only install); extension-free deployment is not a supported profile |
| Review surface | Production design is headless; review is available through stable machine-readable edges | CLI/API/MCP review workflows exist with triage-ordered versioned candidate listings, catalog-derived evidence enforcement, audited assertion revocation, and supersede-on-approve (ADR-038, migration 0019); there is no bundled web review UI | Aligned for headless review; a review UI remains absent by design |
| Semantic projection | Versioned, ACL-filtered shadow spaces are evaluated before explicit activation; the release-reviewed default identity activates when complete (ADR-065) | Every sync and activated re-extraction embeds new or changed units and reports `semantic_projection`; a complete space whose identity is in `RELEASE_REVIEWED_EMBEDDING_IDENTITIES` activates automatically, other identities wait for evaluation and `kip projection activate`. The 2026-08-13 `c4000` Qwen3 space (`30,565/30,565`, HNSW vector Recall@10/MRR `0.947/0.822`, P95 `133.75 ms`, zero ACL leaks) is historical; 3.12.0 evidence is recorded above and in `IMPLEMENTATION_STATUS.md` | Default under ADR-065; the verdict follows the 3.12.0 release evidence, and stale-warning coverage remains a separate gap |
| Semantic operations | Rebuilds are repeatable, bounded, and cannot mix incompatible inputs | Rebuild resumes missing/source-hash-stale rows 1000 at a time, verifies against the same current-unit denominator, and versions the `head_tail_v1` 4,000-character input policy in the space identity. Embedding requests are bounded by `batch_size` and `max_batch_chars`; a per-adapter circuit and a separate 10 s query timeout keep a failed runtime off the query path. The first full projection of a large corpus takes hours (about three for about 176,500 units on the reference Apple Silicon). Rows store source hash but not the final normalized per-input hash required by `FR-VEC-002` | Partial after ADR-035/065; add the input-hash contract |
| Answer and refusal | Exact fresh evidence produces cited claims; insufficient or ambiguous evidence produces a typed refusal | Search, exact reopen, freshness, citation validation, and optional generation are shared. Identifier, numeric, focused-fact, and short multi-document adequacy gates return `answer_not_present` or `clarification_required`; targeted regressions pass. An entire-query exact `file://` basename returns that document's cited extracts, and allowed same-name files with differing content refuse with `clarification_required` before the result limit; reads, context items, and citations report `stat`/`sha256`/`unavailable` verification and context discloses truncation (ADR-059) | Aligned for bounded pilot; broad reviewed end-to-end calibration remains, and the filename path has no measured answer-quality evaluation |
| Ontology RAG and graph | Only reviewed assertions enter current graph context; every path reopens exact, ACL-visible evidence; Neo4j is optional | Typed candidates, mining/review, migration lineage, approved PostgreSQL graph context, exact evidence, and evaluation contracts exist. The curation loop is executable end to end (ADR-038): approved-entity-aware re-mining, per-proposal skip reporting on durable job results, reversible review, and opt-in clearly-marked candidate visibility. Approving an entity-type or predicate discovery candidate now materializes an additive, shadow-validated ontology release automatically (ADR-044; long-running processes need a restart to load it, per-invocation CLI does not). Graph traversal runs inside the active repository backend (`capabilities.graph_backend` reports it; Neo4j is a future optional read projection with no shipped scaffolding), and broad private ontology quality has not been established | Ready for pilot, not broad ontology-RAG acceptance |
| Observability | Vendor-neutral redacted traces/metrics expose pipeline decisions without creating a second sensitive corpus | Closed PostgreSQL query traces, admin inspection/pruning, and optional OTLP spans/metrics are implemented, and `ops-report.sh` adds a schedulable single-command health summary (failed jobs, queue age, sync age, disk, backup age, `/readyz`) with an optional failure webhook. Collector delivery is non-blocking; dashboards, paging, and alert push beyond the webhook are deployment responsibilities | Aligned for pilot; push alerting and dashboards remain environment-specific |
| Backup and recovery | PostgreSQL, CAS, configuration, migrations, ACL/RLS, and rebuildable projections restore into an empty target with fingerprints | Sealed backup/restore tooling, `--retain` pruning, a redacted configuration snapshot with seal-and-verify rescan, a launchd daily schedule, and a checksummed restore drill are implemented; the 2026-08-10 acceptance restored canonical data and a real sealed set was checksum-verified on this host on 2026-08-13. Encryption, off-host retention, and push alerting beyond the ops-report webhook are external | Ready for operational adoption, not a managed backup service |
| Packaging and private handoff | Versioned source/wheel/images/Compose/contracts/skills/SBOM/provenance/checksums contain no private corpus or credentials | The online handoff has a deterministic allowlisted single-root source ZIP with strict manifest, internal checksums, external digest, archive safety limits, and private/secret scan (ADR-052). Clean core-wheel and hardened-image smoke, deterministic production release bundle, SBOM, provenance, image locks, and tag-only publish workflow also exist. Optional extractor modules no longer block base CLI startup, core/runtime dependency parity is tested, the production lock includes `psutil` and MCP 2.0, CI extras are checked against declared extras, and runtime plus all-extra installed dependency audits are clean. A source ZIP or local/branch candidate does not prove the remote tagged publish path ran. Archive manifests now carry the source `repository` next to `git_commit`, normalized so a remote access token or local clone path is dropped rather than shipped, and `DEPLOYMENT_GUIDE` section 11 records the upgrade boundary: which paths a newer package replaces, which belong to the deployment, and that `.mcp.json` sits in both sets and must not be overwritten | Source handoff aligned; production release remains candidate-ready and publication requires per-tag evidence. The v3.5.0 tag workflow completed and its public GitHub Release was read back on 2026-09-10; 3.9.0 publishes a one-command `install.sh` release asset and an in-place `./scripts/upgrade.sh` that applies that boundary from the manifest diff, with `--dry-run`, `var/upgrades/<id>/` rollback and refusal of downgrades, git checkouts and digest/manifest mismatches (ADR-062); deployments created before 3.9.0 still take the manual section 11.5 procedure once, a file rollback does not restore the database, and there is still no update notification for the KIP version itself (`check-upstream-updates.sh` watches pinned third-party parser and model revisions only); 3.10.0 renames the distributable to the KIP package, installs a global `kip` launcher through an opt-out marked shell-profile block, adds `kip update` over the same upgrader, and keeps pre-3.10.0 deployments upgrading through the legacy manifest and asset names (ADR-063) |
| ACL negative testing | Unauthorized principals and scopes reveal neither content nor existence | Repository/RLS probes reported zero leaks. Explicit local CLI scope options replace ambient grants, and an explicit empty set remains empty. A real-Postgres integration test (`test_postgres_cross_scope_acl_denies_search_read_and_graph`) now proves cross-scope denial with two distinct ACL scopes across search, direct unit read, and graph neighbors/path, so a regression in the RLS policies or the SQL ACL predicates fails the suite | Aligned at repository and local edge boundaries |
| Production acceptance | Blank-environment setup, 100+ reviewed questions, end-to-end answer/refusal evidence, restore, latency, and all edge checks pass | Distribution and portable contracts are implemented, but the reviewed private retrieval set has 19 cases, stale-warning evidence is absent, answer coverage is not broad, and environment-specific connectors/providers require validation | Not broad-production accepted |

## Current measured retrieval snapshot

The 3.12.0 release evidence for the default mode and its lexical fallback is
recorded in the 3.12.0 paragraph of "Current alignment" and in
`IMPLEMENTATION_STATUS.md`. The table below is the authoritative historical
`c4000` semantic decision,
`evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`; it
predates the 3.12.0 lexical and fusion changes.

| Variant | Recall@10 | MRR | P95 | ACL leaks |
|---|---:|---:|---:|---:|
| Active lexical + BM25 | 0.789 | 0.646 | 7385.80 ms | 0 |
| Vector HNSW shadow | **0.947** | **0.822** | **133.75 ms** | 0 |
| Hybrid shadow | 0.895 | 0.702 | 7924.98 ms | 0 |
| Reranked shadow | 0.842 | 0.656 | 15512.58 ms | 0 |

The vector channel solves a real candidate-recall problem: semantic-paraphrase
Recall@10 improves from `0.429` to `0.857`, while exact identifier and exact
numeric Recall@10 remain `1.000`. That is retrieval evidence only. It does not
establish final-answer correctness, citation quality, refusal appropriateness,
or an industry-leading result.

## Context-free manual QA snapshot

These 2026-08-13 probes used fresh agents without KIP command context. They are
manual observations, not an immutable golden report, and therefore cannot be
used as a promotion gate.

| Scenario | Observation | Disposition |
|---|---|---|
| Exact identifier | Expected evidence retrieved | Pass |
| Exact numeric | Expected evidence retrieved | Pass |
| Narrow XLSX exact read | Original range read succeeded | Pass |
| Supplier paraphrase | Relevant evidence was lexical rank 9; extractive answer selected unrelated content | Retrieval/answer gap |
| Korean-English/code-switched query | Relevant evidence was rank 10 and can fall outside a smaller default result window | Candidate-recall gap |
| Related words but no factual answer | Focused-fact adequacy regression cases now return `answer_not_present` | Bounded fix; expand reviewed cases |
| Unknown identifier | Search may show low-score discovery hits, while the answer edge now returns `answer_not_present` | Safe answer behavior; search display remains a quality issue |
| Ambiguous question | Short multi-document answer requests now return `clarification_required` | Bounded fix; expand reviewed cases |
| Outsider ACL | Explicit singular, plural, and empty scope inputs replace the ambient value and return no unauthorized result | Pass |
| Wide XLSX range with typed cells | Date/datetime/time/duration values, cached formula dates, array/data-table formulas, non-finite numerics, merges, filters, and blank padding remain strict JSON with exact coordinates | Pass at parser, storage-validation, and contract boundaries |
| SolarEdge PPTX corpus | 55/55 files parsed; 52 succeeded, two surfaced `SKIPPED_OLE`, one surfaced `SKIPPED_MEDIA`; 77,922 native units serialized and source stats were unchanged. Korean OCR added 152 located units across two screenshot-heavy decks with unchanged sources and explicit low-confidence warnings | Native parser and OCR integration pass; reviewed CER/WER, retrieval quality, media/OLE, and legacy `.ppt` coverage remain |

## Ordered next gates

1. Run the reviewed floor on a protected corpus-bearing pre-merge runner with
   `KIP_REQUIRE_PRIVATE_GOLDEN=1`.
2. Add stale-warning coverage to a fingerprinted report of the default semantic
   mode, and replace the Infinity 0.0.77 runtime (transformers below 5) once a
   maintained successor passes the same identity and quality gates.
3. Expand to at least 30-50 reviewed private pilot questions, then 100+ plus a
   frozen holdout for broad rollout.
4. Expand end-to-end answer/citation/refusal and ambiguity cases beyond the
   bounded adequacy regressions, especially code-switched and answerless queries.
5. Store the normalized final per-row embedding-input hash required by
   `FR-VEC-002` and revalidate resumable rebuild semantics.
6. Complete HWP locator, OCR, restore, connector/provider, pagination,
   push-alerting, and recipient-environment acceptance evidence; breaking ontology
   changes still require a reviewed migration; additive discovery approvals
   already materialize YAML releases (ADR-044).
