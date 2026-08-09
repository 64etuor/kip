# Implementation Status

| Area | Status | Notes |
|---|---|---|
| Root agent files | Ready | `AGENTS.md`, `CLAUDE.md`, `.mcp.json` included |
| Canonical contracts | Ready | Pydantic models and generated JSON Schema |
| Production distribution | Ready | Digest-pinned non-root image, read-only Compose profile, locked runtime/build inputs, wheel, image lock, SPDX SBOM, SLSA provenance, deterministic archive, private-data scan, and directory/archive verifier |
| CI supply-chain gates | Ready | SHA-pinned actions, Python 3.12/3.13 matrix, contracts, architecture, Ruff, mypy, dependency audit, migrations, tests, 75% coverage, clean-wheel smoke, hardened-image smoke, candidate bundle, and tag-only GHCR publish with attestations |
| Backup and recovery | Ready for operational adoption | Sealed PostgreSQL/CAS/config backup, `row_security=off` manifest, explicit empty-target restore, row/migration/extension/RLS/CAS comparison, projection rebuild, fingerprinted evaluation comparison, and checksummed drill receipt |
| Memory repository | Ready | Used for tests and offline smoke checks |
| PostgreSQL migrations | Ready for pilot | Workspace, required-scope, and ACL-snapshot freshness RLS included; test with non-owner production roles |
| PostgreSQL repository | Pilot reference | Core ingest, search, exact read, ACL, job, assertion, export, and rebuild methods implemented; evidence and graph reads are ACL- and freshness-prefiltered |
| CLI | Ready for pilot | JSON-first commands; source-neutral `sync run`, top-level `xlsx-read`, projection and canonical export aliases |
| REST API | Ready for pilot | Read, exact evidence, assertion explain, connector event, sync, and review endpoints; trusted API-key or verified JWT identity |
| Identity and ACL snapshots | Ready for pilot | JWT issuer/audience/JWKS verification, configured API-key principal, stale dynamic snapshot exclusion, and legacy identity-header rejection |
| Data classification and model egress | Ready for pilot | Canonical source/unit classification, local loopback policy, remote provider/classification/retention/secret gates, and atomic denial decisions |
| Structured generation adapters | Ready for pilot | Provider-neutral typed contract with bounded HTTP responses, explicit timeouts, pinned model revisions, request IDs, token accounting, citation-ID validation, and OpenAI Responses/Anthropic Messages adapters |
| MCP | Reference adapter | Requires optional dependency and client validation |
| Filesystem connector | Ready for pilot | Read-only traversal, hash, settle-time checks |
| XLSX shallow/deep | Ready for pilot | Shared-string shallow index and exact `.xlsx`/`.xlsm` range reader; formula/cached values, formats, dates, and hidden dimensions are explicit |
| PDF parser | Ready for pilot | PyMuPDF; OCR is routed but not bundled |
| HWP broker | Partial pilot | Native HWP/HWPX signatures and real-file text extraction are validated; true section/paragraph/table locators and broad shadow re-extraction remain incomplete |
| Slack connector | Reference adapter | Validate scopes, rate limits, edits/deletes, and retention |
| Apple Mail connector | Reference adapter | macOS permission and mailbox allowlist required |
| IMAP connector | Reference adapter | Validate provider-specific UID behavior |
| Public evaluation corpus | Ready | Six checksum-pinned KOGL Type 1 PDFs; 30 relevance and 6 ACL cases |
| Evaluation reports | Ready with coverage gaps | Retrieval, answer, and ontology metrics; immutable dataset/review binding; full-case coverage gates; ACL/integrity checks; fingerprints; Markdown scorecards; append-only ledger; public locator/latest/stale/recovery and end-to-end reviews remain incomplete |
| Quality control plane | Ready for pilot | Version-pinned parser/embedding/reranker/retrieval experiment manifests and fail-closed, read-only promotion recommendations; manifest-driven orchestration is not yet a scheduler |
| End-to-end RAG rubric | Ready for pilot | Deterministic claim/citation/refusal and entity/relation/evidence/contradiction/path/temporal/integrity metrics; missing reviews fail closed and the bundled ontology case is synthetic contract evidence only |
| Query tracing and metrics | Ready for pilot | PostgreSQL/RLS canonical redacted traces, admin-only CLI/REST inspection, bounded retention pruning, non-fatal delivery, and optional OTLP/HTTP spans and metrics without content attributes |
| Evidence-bounded answer | Ready for pilot | CLI/API/MCP/SDK share search, exact reopen, freshness, XLSX, classification/egress, structured generation, claim-citation validation, typed refusal, explicit extractive fallback, and ACL-filtered current approved-graph context; candidates, expired relations, and stale graph evidence are excluded |
| Local embedding sidecar | Validated shadow | Infinity 0.0.77, Qwen3 0.6B 1024d, pinned revisions, MPS smoke passed |
| Local reranker | Validated shadow | BGE reranker v2 M3 plus opt-in pinned Jina Hugging Face adapter; Jina trial measured 613.31 ms P95 but failed quality gates, so remains shadow-only |
| pgvector | Complete shadow, disabled | PostgreSQL 18/pgvector 0.8.2, 74/74 vectors, RLS and source-hash filtering; public pilot did not beat lexical |
| Hybrid retrieval | Complete shadow | ACL-prefiltered exact vector search, RRF, bounded reranking, explicit activation command |
| Ontology contract | Ready for pilot | YAML entity inheritance and predicate contracts; ACL-bound mining jobs; strict structured-output validation; reviewed entities/relations; exact evidence; deterministic fingerprints; current approved-graph answers; and idempotent predicate migration materialization with source-assertion lineage |
| Neo4j | Port only | Do not deploy before adoption gate |
| Review UI | Not included | CLI/API review workflow only |
| Starter-kit adoption guide | Ready | Environment decisions, AI change contract, real-corpus acceptance evidence, upgrade and handoff rules |
| Upstream update watch | Ready for GitHub-hosted repositories | Dependabot covers Python/Actions/Docker; scheduled workflow reports pinned kordoc and Hugging Face model revision changes without activation |

## Explicit pilot limitations

- Release archives are deliberately not encrypted, scheduled, uploaded, or
  retained by KIP. Production must provide an external secret manager,
  encrypted off-host backup policy, identity-aware TLS edge, and alerting.
- Tag publishing requires repository permissions for GHCR packages, OIDC, and
  GitHub attestations. A locally verified candidate does not prove that the
  repository's tag-only publication path has run.
- `compose.production.yaml` is a hardened reference, not an orchestrator or
  secret manager. Operators must supply non-owner database login URLs, regular
  secret files, immutable image digests, storage, TLS/IAP, and deployment-level
  rollback and monitoring.
- The default sync mode is incremental. Forced full re-extraction and destructive source reconciliation are intentionally not exposed as one-step starter commands.
- The PostgreSQL integration test is gated by `KIP_TEST_POSTGRES_URL`; CI or a local PostgreSQL service must run it before deployment.
- Multi-user production requires an identity-aware proxy that issues the
  configured JWT claims. KIP verifies those claims directly and rejects legacy
  caller identity/ACL headers; target-provider revocation latency is bounded by
  the shorter of token and ACL-snapshot expiry.
- Remote model use requires an approved provider contract and classification
  allowlist. The `zero_retention` field is configuration evidence, not
  independent verification of the provider account setting.
- The bundled remote generators disable ambient proxy discovery and require an
  explicit endpoint, immutable model revision, and environment-backed secret
  reference. Keychain and external secret-manager resolution require a runtime
  integration and fail closed in the reference container.
- Optional HWP parser commands, Slack scopes, Apple Mail Automation permissions, and IMAP provider behavior must be validated against the target environment.
- Neo4j remains an adoption-gate adapter stub; canonical assertions are queried from PostgreSQL.
- The current public pilot is small and lexically distinctive. Its `keep_disabled` decision must not be generalized to a private corpus without adding reviewed internal golden cases. The private OneDrive shadow A/B also showed no gain over lexical retrieval on its small golden set, so semantic projection remains shadow-only.
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
