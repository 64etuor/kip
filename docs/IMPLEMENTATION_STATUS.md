# Implementation Status

| Area | Status | Notes |
|---|---|---|
| Root agent files | Ready | `AGENTS.md`, `CLAUDE.md`, `.mcp.json` included |
| Canonical contracts | Ready | Pydantic models and generated JSON Schema |
| Memory repository | Ready | Used for tests and offline smoke checks |
| PostgreSQL migrations | Ready for pilot | Workspace, required-scope, and ACL-snapshot freshness RLS included; test with non-owner production roles |
| PostgreSQL repository | Pilot reference | Core ingest, search, exact read, ACL, job, assertion, export, and rebuild methods implemented; evidence and graph reads are ACL- and freshness-prefiltered |
| CLI | Ready for pilot | JSON-first commands; source-neutral `sync run`, top-level `xlsx-read`, projection and canonical export aliases |
| REST API | Ready for pilot | Read, exact evidence, assertion explain, connector event, sync, and review endpoints; trusted API-key or verified JWT identity |
| Identity and ACL snapshots | Ready for pilot | JWT issuer/audience/JWKS verification, configured API-key principal, stale dynamic snapshot exclusion, and legacy identity-header rejection |
| Data classification and model egress | Ready for pilot | Canonical source/unit classification, local loopback policy, remote provider/classification/retention/secret gates, and atomic denial decisions |
| Structured generation adapters | Ready for pilot | Provider-neutral typed contract with bounded HTTP responses, explicit timeouts, pinned model revisions, request IDs, token accounting, citation-ID validation, and OpenAI Responses/Anthropic Messages adapters; answer orchestration remains separately gated |
| MCP | Reference adapter | Requires optional dependency and client validation |
| Filesystem connector | Ready for pilot | Read-only traversal, hash, settle-time checks |
| XLSX shallow/deep | Ready for pilot | Shared-string shallow index and exact `.xlsx`/`.xlsm` range reader; formula/cached values, formats, dates, and hidden dimensions are explicit |
| PDF parser | Ready for pilot | PyMuPDF; OCR is routed but not bundled |
| HWP broker | Partial pilot | Native HWP/HWPX signatures and real-file text extraction are validated; true section/paragraph/table locators and broad shadow re-extraction remain incomplete |
| Slack connector | Reference adapter | Validate scopes, rate limits, edits/deletes, and retention |
| Apple Mail connector | Reference adapter | macOS permission and mailbox allowlist required |
| IMAP connector | Reference adapter | Validate provider-specific UID behavior |
| Public evaluation corpus | Ready | Six checksum-pinned KOGL Type 1 PDFs; 30 relevance and 6 ACL cases |
| Evaluation reports | Ready with coverage gaps | Recall, MRR, nDCG, ACL, latency, failure counts, fingerprints, Markdown scorecard, append-only ledger; locator/latest/stale/recovery remain unmeasured in the public set |
| Quality control plane | Ready for pilot | Version-pinned parser/embedding/reranker/retrieval experiment manifests and fail-closed, read-only promotion recommendations; manifest-driven orchestration is not yet a scheduler |
| Answer-quality rubric | Contract ready | Deterministic reviewed groundedness, completeness, citation-locator, unsupported-claim, and refusal metrics; current golden datasets still need answer annotations |
| Evidence-bounded answer | Ready for pilot | CLI/API/MCP exact-evidence extractive answer with ACL, relevance, freshness, refusal, and XLSX deep-read gates; structured generation exists but is not yet connected to this path |
| Local embedding sidecar | Validated shadow | Infinity 0.0.77, Qwen3 0.6B 1024d, pinned revisions, MPS smoke passed |
| Local reranker | Validated shadow | BGE reranker v2 M3 plus opt-in pinned Jina Hugging Face adapter; Jina trial measured 613.31 ms P95 but failed quality gates, so remains shadow-only |
| pgvector | Complete shadow, disabled | PostgreSQL 18/pgvector 0.8.2, 74/74 vectors, RLS and source-hash filtering; public pilot did not beat lexical |
| Hybrid retrieval | Complete shadow | ACL-prefiltered exact vector search, RRF, bounded reranking, explicit activation command |
| Ontology contract | Ready for pilot | YAML consistency and candidate checks plus compatible/review/breaking release diff and explicit migration coverage; assertion migration execution remains review-queue work |
| Neo4j | Port only | Do not deploy before adoption gate |
| Review UI | Not included | CLI/API review workflow only |
| Starter-kit adoption guide | Ready | Environment decisions, AI change contract, real-corpus acceptance evidence, upgrade and handoff rules |
| Upstream update watch | Ready for GitHub-hosted repositories | Dependabot covers Python/Actions/Docker; scheduled workflow reports pinned kordoc and Hugging Face model revision changes without activation |

## Explicit pilot limitations

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
- Answer-quality metrics are available, but retrieval-only reports cannot claim end-to-end RAG quality until reviewed claim/citation/refusal annotations are populated.
- Ontology diff validates migration coverage but does not yet materialize target-version assertion candidates in PostgreSQL. Existing approved assertions are never rewritten in place.

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
