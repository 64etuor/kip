# Implementation Status

This is the current readiness inventory, not the target architecture. The
target-to-current matrix and ordered gap register live in
`docs/PRODUCTION_DESIGN_ALIGNMENT.md`.

| Area | Status | Notes |
|---|---|---|
| Root agent files | Ready | `AGENTS.md`, `CLAUDE.md`, `.mcp.json` included |
| Canonical contracts | Ready | Pydantic models and generated JSON Schema |
| Production distribution | Ready | Digest-pinned non-root image, read-only Compose profile (with the one deliberate exception of the `${KIP_ONTOLOGY_PATH}` bind mounted read-write into the API for discovery auto-release, ADR-044), locked runtime/build inputs, wheel, image lock, SPDX SBOM, SLSA provenance, deterministic archive, private-data scan, and directory/archive verifier |
| CI supply-chain gates | Ready | SHA-pinned actions, Python 3.12/3.13 matrix, contracts, architecture, Ruff, mypy, dependency audit, migrations, tests, 75% coverage, clean-wheel smoke, hardened-image smoke, candidate bundle, and tag-only GHCR publish with attestations |
| Backup and recovery | Ready for operational adoption | Sealed PostgreSQL/CAS/config backup, `row_security=off` manifest, explicit empty-target restore, row/migration/extension/RLS/CAS comparison, projection rebuild, fingerprinted evaluation comparison, and checksummed drill receipt. `--retain N` pruning (default 7), a redacted configuration snapshot with a seal-and-verify secret rescan, and a launchd daily schedule (`com.kip.backup`; the installer supports `--dry-run`) are included; a real sealed set was produced and checksum-verified on this host on 2026-08-13 (`var/backups/20260813T070625Z`) |
| Host operations reporting | Ready for pilot | `scripts/ops-report.sh` summarizes failed jobs, oldest queue age, last sync progress age, disk free, newest backup age, and API health (`/readyz` with `/healthz` fallback) with tunable thresholds, `--json`, an optional `KIP_OPS_WEBHOOK` failure POST, and an optional launchd item; `install-launchd.sh` also guards against a double worker and renders a newsyslog rotation policy |
| Memory repository | Ready | Used for tests and offline smoke checks |
| PostgreSQL migrations | Ready as the production reference profile | Workspace, required-scope, ACL-snapshot freshness, and owner-bound `FORCE ROW LEVEL SECURITY` are included. Normal migration installs pgvector, the 1024d projection, and migration 0018's cosine HNSW index; semantic activation remains separate |
| PostgreSQL repository | Pilot reference | Core ingest, search, exact read, ACL, job, assertion, export, and rebuild methods implemented; evidence and graph reads are ACL- and freshness-prefiltered; repository calls share a bounded connection pool (`database.pool_max_size`). Re-syncing unchanged files no longer fails: configuration-owned ACL-snapshot timestamps refresh while snapshot identity fields stay strictly verified (previously every second `kip sync run` raised a per-file ConflictError) |
| CLI | Ready for pilot | JSON-first commands; source-neutral `sync run`, top-level `xlsx-read`, projection and canonical export aliases; operator roles come only from explicit `--role`/`--roles`/`KIP_ROLES` and admin commands fail closed. Search exposes the full canonical request including mode and filters; explicit ACL options replace ambient scopes |
| REST API | Ready for pilot | Read, exact evidence, assertion explain, connector event, sync, and review endpoints, including the versioned candidate listing, assertion revocation, and a `/readyz` readiness endpoint that round-trips the database (`/healthz` stays liveness-only); trusted API-key or verified JWT identity; blocking handlers run synchronously in the server threadpool instead of on the event loop |
| Python SDK | Ready as a thin REST client | Capabilities, search, context, answer, evidence, graph, ontology, jobs, and review helpers delegate to REST. Search, context, and answer expose the canonical mode and filter set while omitted defaults stay absent from payloads |
| Identity and ACL snapshots | Ready for pilot | JWT issuer/audience/JWKS verification, configured API-key principal, stale dynamic snapshot exclusion, and legacy identity-header rejection |
| Data classification and model egress | Ready for pilot | Canonical source/unit classification, local loopback policy, remote provider/classification/retention/secret gates, and atomic denial decisions |
| Structured generation adapters | Ready for pilot | Provider-neutral typed contract with bounded HTTP responses, explicit timeouts, pinned model revisions, request IDs, token accounting, citation-ID validation, and OpenAI Responses/Anthropic Messages adapters. Claims support a `disputed` certainty that must cite every disagreeing evidence unit, and the prompt instructs the model to surface source conflicts instead of picking one |
| MCP | Ready for pilot | Optional stdio adapter shares the application services; guided setup points `.mcp.json` at the host-path `config/kip.host.generated.toml`, `kip_jobs` and `kip_ontology_assertion_revoke` tools cover job results and review governance, and real client discovery/search/graph/answer parity has been validated |
| Filesystem connector | Ready for pilot | Read-only traversal with an mtime settle window; content hashes are lazy and unchanged files are skipped by size/mtime against the stored revision without being read. Deletion is reconciled by a complete-scan grace policy (migration 0020, `[sync] deletion_grace_scans` default 2): absent files are soft-tombstoned through the shared ingest path, failed and empty scans never contribute deletion evidence, and reappearing files clear their absence mark and re-index; the sync summary reports `absent` and `tombstoned` counts |
| XLSX shallow/deep | Ready for pilot | Shared-string shallow index and exact-shape `.xlsx`/`.xlsm` range reader; JSON-safe scalar/cached values, normal/array/data-table formulas, Excel serials/formats, merged cells, and hidden/filtered dimensions are explicit. Date/datetime/time use ISO 8601, durations use ISO 8601 duration strings, non-finite numerics stay labeled rather than becoming null, and dense validated ranges are capped at 100,000 cells |
| PDF parser | Ready for pilot | PyMuPDF native pages plus default Kordoc 4.7.3 PP-OCRv5 Korean candidate enrichment in new reference installs. Bootstrap and the production image install the exact runtime, verify the model cache, and index offline. Low-text/private-use/control/replacement thresholds, page/bbox locators, exact version checks, and native fallback are implemented. A real scanned drawing recovered 2 OCR units and 864 characters with unchanged source SHA/size/mtime; low-confidence warnings still require review |
| PPTX parser | Ready for retrieval pilot | `python-pptx` plus bounded OOXML scan and default Korean picture OCR in new reference installs; text, merged tables, sparse chart caches, image metadata/hash, nested groups, notes, legacy comments, SmartArt text, hidden slides, geometry, source z-order, and derived reading order are structured. A read-only SolarEdge `5_PROJECT` run parsed 55/55 PPTX files into 77,922 JSON-valid native units with zero source-stat changes. OCR QA added 28 located units from seven images in `GEN2 적용 예시.pptx` and 124 from thirteen images in `FAT문제점 및 차트들.pptx`, with unchanged sources and explicit low-confidence warnings. No legacy `.ppt`, media transcription, modern threaded comments, or OLE expansion |
| HWP broker | Ready for retrieval pilot | Native HWP/HWPX signatures and 86/86 real-file extraction are validated; guarded shadow/atomic activation preserves prior extractions. Parser 1.1 chunks with a 400-char overlap so boundary-spanning facts stay retrievable (86/86 re-extracted and activated). Kordoc-compatible command output preserves structured table/image/span/footnote/list/link metadata and warning page context. True section/paragraph/table locators remain incomplete |
| Slack connector | Reference adapter | Threads are ingested as one semantic event keyed on the root message (replies become revisions); validate scopes, rate limits, edits/deletes, and retention |
| Apple Mail connector | Reference adapter | macOS permission and mailbox allowlist required |
| IMAP connector | Reference adapter | Validate provider-specific UID behavior |
| Public evaluation corpus | Ready | Six checksum-pinned KOGL Type 1 PDFs; 30 relevance and 6 ACL cases |
| Evaluation reports | Ready with coverage gaps | Retrieval, answer, and ontology metrics; immutable dataset/review binding; full-case coverage gates; ACL/integrity checks; fingerprints; Markdown scorecards; append-only ledger; search hits now carry `is_latest` and the runner reopens evidence for stale-warning measurement, so both dimensions are measurable; judge-proposed dataset growth (ADR-045: `kip evaluate draft validate/review/promote` with fingerprint-bound human sample-audit and fail-closed promotion) unblocks growing reviewed sets past the current 19 cases; public locator/recovery and end-to-end reviews remain incomplete |
| Retrieval regression gate | Active in hosted CI and private runners | The checked-in portable manifest expands to 100 positive and 20 ACL-negative cases and always runs in CI/verify. `scripts/golden_gate.py` checks the reviewed private floor; protected runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1` so missing corpus evidence fails closed |
| Quality control plane | Ready for pilot | Version-pinned parser/embedding/reranker/retrieval experiment manifests and fail-closed, read-only promotion recommendations; manifest-driven orchestration is not yet a scheduler |
| End-to-end RAG rubric | Ready for pilot | Deterministic claim/citation/refusal and entity/relation/evidence/contradiction/path/temporal/integrity metrics; missing reviews fail closed and the bundled ontology case is synthetic contract evidence only |
| Query tracing and metrics | Ready for pilot | PostgreSQL/RLS canonical redacted traces, admin-only CLI/REST inspection, bounded retention pruning, non-fatal delivery, and optional OTLP/HTTP spans and metrics without content attributes |
| Adaptive ontology and interaction memory | Ready for pilot | Empty starter profile, one-question setup selection, TTL owner-scoped clarifications, confirmed preferences, structured non-trace feedback, per-principal discovery candidates, PostgreSQL RLS, and CLI/REST/MCP parity. Admin approval of an entity-type or predicate discovery candidate materializes an additive ontology release automatically (ADR-044): shadow-validated, comment-preserving, idempotent targeted YAML edits with a minor version bump and review-policy sync; auto-released predicates default to `review: required`/`risk: high`. Long-running API/worker/MCP processes report `catalog_refresh: "restart_required"` and pick up the release on restart; each CLI invocation sees it immediately. The shipped configurations enable `interaction.enabled` and `ontology.adaptive_discovery` by default |
| Evidence-bounded answer | Ready for bounded pilot; broad quality gate pending | CLI/API/MCP/SDK share search, exact reopen, freshness, XLSX, egress, generation validation, citations, extractive fallback, and approved-graph context. Identifier, numeric, focused-fact, and short multi-document adequacy gates return typed `answer_not_present` or `clarification_required`; broader reviewed answer/citation/refusal coverage remains required |
| Local embedding sidecar | Validated shadow | Infinity 0.0.77, Qwen3 0.6B 1024d, pinned revisions, MPS smoke passed; resumable projection uses current active ACL-fresh units and versioned bounded input. The private space is 30,565/30,565 complete; vector Recall@10/MRR is 0.947/0.822 with P95 133.75 ms and zero ACL leaks. It remains shadow only because stale-warning evidence is absent (`evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`) |
| Local reranker | BM25 active; RapidFuzz fallback; model adapters shadow | RapidFuzz 3.14.5 reranks bounded ACL-filtered lexical candidates locally and passed the private OneDrive retrieval gate; BGE/Jina model adapters remain opt-in shadow candidates. A candidate-local Okapi BM25 backend (`models.reranker.backend = "bm25"`, word+bigram Korean tokens, no model or extension dependency) beat RapidFuzz on the 19-case grounded draft set (Recall@10 0.842 vs 0.684, MRR 0.639 vs 0.566, lower P95; see `evaluation/reports/reranker-ab-20260811/decision.md`) and was promoted on 2026-08-11 after the dataset was adversarially re-verified and versioned (`reviewed 1.0.0`, ADR-034); RapidFuzz remains the fallback backend |
| Search result diversity | Active | Per-document cap (`search.max_hits_per_document`, default 3) with tail backfill across every search path, so one file cannot occupy all result slots |
| pgvector and HNSW | Production-profile ready; semantic shadow disabled | PostgreSQL 18/pgvector 0.8.2, RLS/source-hash filtering, migration 0018 HNSW with bounded strict iterative scan, and a complete 30,565/30,565 private Qwen3 shadow space. EXPLAIN confirms the HNSW index path; activation is still quality-gated |
| Hybrid retrieval | Complete shadow | ACL-prefiltered exact vector search, RRF, bounded reranking that preserves the un-reranked fused tail up to the request limit, explicit activation command. On the reviewed 19-case private set, vector-only Recall@10/MRR was 0.947/0.822, ahead of hybrid at 0.895/0.702 and reranked at 0.842/0.656 |
| Alias query expansion | Active for the lexical path | Human-approved entity aliases (ACL-prefiltered `resolve_entities`) expand candidate retrieval only; reranking keeps the user's original wording. It lifted RapidFuzz on the grounded draft set and is aggregate-neutral-to-positive under the now-active BM25 backend; re-evaluate if candidate generation changes (`evaluation/reports/alias-expansion-20260811/decision.md`) |
| Ontology contract | Ready for pilot | YAML entity inheritance and predicate contracts; collision-safe validation (ADR-043: a domain profile redefining a core entity type or predicate, or a `sources/*.yaml` object type with an unknown parent, fails `kip ontology validate` and container startup); ACL-bound mining jobs; strict structured-output validation; reviewed entities/relations; exact evidence; deterministic fingerprints; current approved-graph answers; and idempotent predicate migration materialization with source-assertion lineage. The curation loop is reviewable end to end (ADR-038): approved-entity-aware mining digests make the two-pass mine -> approve entities -> re-mine loop run, invalid/duplicate/stale proposals are skipped with per-proposal reasons on a durable `kip.ontology-mining-result.v1` job payload, evidence/review enforcement is derived from the catalog and pinned to `predicates.yaml` by a contract test, candidate listings ship as triage-ordered `kip.assertion-candidate-listing.v1` with Korean labels and ACL-gated snippets, audited revocation and supersede-on-approve exist (migration 0019), and `include_candidate_assertions` populates clearly-marked proposed candidates on ontology-context surfaces only |
| Agent-guided setup | Ready for starter acceptance | Fail-fast Python 3.12+ bootstrap, `env:`/`file:`-only secret references rejected at answer time, runtime-readiness verify checks (python, docker, DB secret, source readability) that never flip `verified` on environmental failures, receipt `next_steps` and a configuration-only limitation entry, host-path MCP config, and `scripts/app-up.sh` layering `compose.generated.yaml` over the base Compose file; setup remains configuration-only until the next steps run |
| Neo4j | Port only | Do not deploy before adoption gate |
| Review UI | Not included | CLI/API review workflow only |
| Starter-kit adoption guide | Ready | Environment decisions, AI change contract, real-corpus acceptance evidence, upgrade and handoff rules |
| Upstream update watch | Ready for GitHub-hosted repositories | Dependabot covers Python/Actions/Docker; a behavior-tested daily workflow reads the OCR `expected_version`, reports Kordoc and Hugging Face revision drift in one GitHub issue, and closes it when pins match again without activating an update |

## Explicit pilot limitations

- Backup archives are deliberately not encrypted or uploaded off-host by KIP.
  Local daily scheduling (`com.kip.backup`) and `--retain` pruning now exist on
  the macOS host, but production must still provide an external secret manager,
  an encrypted off-host backup policy, an identity-aware TLS edge, and push
  alerting; the only built-in alert path is the optional `ops-report.sh`
  failure webhook (`KIP_OPS_WEBHOOK`).
- Tag publishing requires repository permissions for GHCR packages, OIDC, and
  GitHub attestations. A locally verified candidate does not prove that the
  repository's tag-only publication path has run.
- `compose.production.yaml` is a hardened reference, not an orchestrator or
  secret manager. It requires `${KIP_NAS_PATH}` bound read-only into both the
  worker and the API (the API opens the live source tree for evidence
  freshness and `xlsx-read`), targets `/readyz` for the API healthcheck, and
  probes worker database connectivity; operators must still supply non-owner
  database login URLs, regular secret files, immutable image digests, storage,
  TLS/IAP, and deployment-level rollback, dashboards, and paging.
- The default sync mode is incremental. HWP/HWPX has an explicit non-mutating
  `parser reextract` shadow command and a separate guarded `--activate` action.
  Generic all-format forced re-extraction and destructive source
  reconciliation are intentionally not exposed as one-step starter commands.
- Filesystem deletion reconciliation is complete-scan-only and fail-safe: a
  failed or aborted scan marks nothing, and a scan that sees zero files is
  skipped with a warning, so empty scans never contribute deletion evidence —
  a truly emptied source tree is never tombstoned automatically. Narrowing
  `include_extensions` or `exclude_globs` tombstones now-out-of-scope content
  after the grace window, so review collection-scope changes before syncing.
  The TRD's additional sentinel-file, count-drop, and permission-ratio mount
  guards beyond the empty-scan skip are not implemented.
- The `sync_schedule` setup answer is declarative operational metadata only;
  nothing schedules syncs automatically. Periodic execution comes from
  `install-launchd.sh` (which uses its own interval setting) or an external
  scheduler.
- The PostgreSQL integration test is gated by `KIP_TEST_POSTGRES_URL`; CI or a local PostgreSQL service must run it before deployment.
- Pgvector/HNSW is part of the supported PostgreSQL production reference
  profile. A future extension-free distribution would need a separate migration
  and CI matrix; disabling semantic search does not uninstall the extension.
- Canonical search mode and filters now have CLI/REST/MCP/SDK parity, and
  capabilities prove a compatible complete active space rather than
  configuration alone. Current CLI exit statuses still group most typed KIP
  errors under code 3, and list/search edges do not expose cursor pagination.
- The portable 120-case gate blocks deterministic stage/filter/ACL regressions
  in hosted CI. It does not replace the reviewed private corpus; only a protected
  runner with `KIP_REQUIRE_PRIVATE_GOLDEN=1` supports the stronger merge claim.
- Context-free private-corpus QA still shows candidate-recall limits: some
  supplier paraphrases and Korean-English code-switched queries rank relevant
  evidence near or outside a small lexical result window. Conservative
  identifier, numeric, focused-fact, and ambiguity gates now refuse instead of
  presenting unsupported extractive success, but the behavior needs a larger
  reviewed end-to-end set.
- Explicit local CLI ACL values replace ambient `KIP_ACL_SCOPES`, including an
  explicitly empty set. Repository/RLS and local outsider probes observed no
  leak; production identity still comes from the configured adapter.
- Multi-user production requires an identity-aware proxy that issues the
  configured JWT claims. KIP verifies those claims directly and rejects legacy
  caller identity/ACL headers; target-provider revocation latency is bounded by
  the shorter of token and ACL-snapshot expiry.
- Remote model use requires an approved provider contract and classification
  allowlist. The `zero_retention` field is configuration evidence, not
  independent verification of the provider account setting.
- The bundled remote generators disable ambient proxy discovery and require an
  explicit endpoint, immutable model revision, and environment-backed secret
  reference. `keychain:` and `secret-manager:` references are rejected at
  setup answer time with guidance; only `env:` references resolve everywhere,
  `file:` resolves only for the model credential, and the database URL and
  bootstrap identity keys accept `env:` alone.
- Optional HWP parser commands, Slack scopes, Apple Mail Automation permissions, and IMAP provider behavior must be validated against the target environment.
- PPTX structural extraction and default Kordoc 4.7.3/PP-OCRv5 Korean image
  OCR are local and non-executing in new reference installs. The runtime and
  model cache are installed and verified before offline indexing. Audio/video transcription,
  embedded OLE/package expansion, modern threaded comments, formula OCR, and
  legacy binary `.ppt` remain unsupported.
- Neo4j remains an adoption-gate adapter stub; canonical assertions are queried from PostgreSQL.
- The current public pilot is small and lexically distinctive. Its
  `keep_disabled` semantic decision must not be generalized to a private corpus
  without reviewed internal golden cases. The reviewed private OneDrive set now
  shows a material vector gain, including semantic-paraphrase Recall@10
  `0.429 -> 0.857`, with zero exact-recall regression and zero ACL leaks.
  HNSW now preserves those metrics at P95 `133.75 ms`, below the `2000 ms`
  gate. Semantic projection nevertheless remains shadow-only because
  stale-warning coverage is absent and therefore fails closed.
  Separately, the 2026-08-10 native-HWP OneDrive A/B first promoted local
  RapidFuzz on a 253-query source-derived set; ADR-034 superseded that default
  with candidate-local BM25 after the reviewed 19-case comparison.
- The 2026-08-06 loaded-corpus audit is recorded in `docs/RAG_QUALITY_AUDIT_2026-08-06.md`; lexical remains active and all semantic candidates remain shadow-only.
- Quality recommendations do not discover, install, or activate libraries. Candidate dependencies remain opt-in adapters; a scheduler may automate shadow runs only after reproducible manifest execution is added.
- Retrieval-only reports cannot claim end-to-end RAG quality. Promotion requires
  immutable reviewed claim/citation/refusal and ontology observations for every
  case in each declared dimension; the bundled ontology starter is synthetic
  contract evidence, not a private-corpus quality result.
- Predicate rename, replace, split, merge, and deprecate manifests are validated
  before ACL-filtered source assertions are materialized as target-version
  review candidates. Existing assertions remain active until a separate review
  decision. Entity-type migrations with live entities fail closed until an
  identity-history workflow can preserve merge and split semantics.
- Relation mining is opt-in and reuses the configured generation adapter and
  central egress policy. Jobs pin the submitter access snapshot, fail if it
  expires before processing, and never auto-promote entity or relation output.
- Ontology answer context resolves normalized canonical names and aliases from
  the repository index, traverses bounded current approved paths, and reopens
  every assertion evidence unit. It does not provide historical as-of queries;
  expired and future assertions are intentionally absent from normal answers.
- Query trace delivery is deliberately non-blocking. Production monitoring must
  alert on collector gaps and run the retention prune schedule; PostgreSQL
  remains the incident-review source when OTLP delivery is unavailable.
- Interaction memory is intentionally not a self-training system. It stores no
  raw feedback text, does not read during normal retrieval, and never promotes a
  discovery candidate. Operators must schedule expired-clarification pruning and
  use a non-owner API/worker role before multi-user deployment.

## Self-improvement canary: 2026-08-06

The current 18,496-unit PostgreSQL corpus was re-evaluated after adding the
quality control plane. Run `eval_20260806T103708498279Z` used the pinned
36-case public-government dataset and the new code fingerprint
`sha256:e928d33c685e3503a51eeb360953744abe686869046d3ef7637a6a1a1b8660f6`.
Lexical retrieval retained Recall@10 `1.000`, MRR `0.9347`, nDCG@10 `0.9501`,
zero failed cases, and zero ACL leaks; P95 was `630.77 ms`. Locator,
latest-version, stale-warning, and final-answer dimensions remain unmeasured,
so this run is a retrieval canary rather than end-to-end RAG certification.
Artifacts are under `evaluation/reports/self-improving-rag-20260806/`.

The pinned BGE reranker audit report was also processed through the new
recommendation command. It returned `keep_disabled`: Recall did not improve,
P95 was `10029.13 ms` against a `2000 ms` ceiling, and required evidence
metrics were unmeasured. No projection or model activation changed.

## OneDrive native parser and local reranker: 2026-08-10

An isolated PostgreSQL A/B parsed 86/86 real HWP/HWPX files into 263 native
units with zero failures and unchanged source hashes. Across 253 source-derived
queries, PostgreSQL lexical Recall@1/Recall@5/MRR was
`0.9407/0.9881/0.9596`; bounded RapidFuzz reranking reached
`0.9684/0.9960/0.9796` and added `15.072 ms` at P95. RapidFuzz is therefore the
winner for this **historical source-derived experiment**, not the current
starter default. ADR-034 later promoted candidate-local BM25 on the reviewed
19-case set; RapidFuzz remains its fallback. Kiwi, deterministic proximity, and
the Kiwi ensemble remain rejected. The 253 queries were not reviewed
natural-language answer or ontology cases; those dimensions remain explicitly
unmeasured. See
`evaluation/reports/onedrive-hwp-native-rapidfuzz-20260810/decision.json` and
ADR-031.

## Operations hardening evidence: 2026-08-13

A real sealed backup set was produced on this host with retention pruning
enabled: `var/backups/20260813T070625Z` (453 MiB), all `SHA256SUMS` entries
verified OK, and the configuration snapshot passed the seal-and-verify secret
redaction rescan. A live `ops-report.sh` run on the same host flagged the
data volume at 99% used — below the 10% free-disk threshold — which is a real
operator warning, not a tooling defect: free disk space before relying on the
daily backup schedule. Semantic search remains shadow/disabled, Slack and
mail connectors remain disabled reference adapters, and the active reranked
lexical pipeline retains its multi-second golden-gate P95 (about 7.4 s on the
2026-08-13 reviewed 19-case run), so retrieval latency is an open quality
item, not a regression introduced by these operations changes.
