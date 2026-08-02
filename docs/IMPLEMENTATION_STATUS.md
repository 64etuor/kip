# Implementation Status

| Area | Status | Notes |
|---|---|---|
| Root agent files | Ready | `AGENTS.md`, `CLAUDE.md`, `.mcp.json` included |
| Canonical contracts | Ready | Pydantic models and generated JSON Schema |
| Memory repository | Ready | Used for tests and offline smoke checks |
| PostgreSQL migrations | Ready for pilot | Workspace and required-scope RLS included; test with non-owner production roles |
| PostgreSQL repository | Pilot reference | Core ingest, search, exact read, ACL, job, assertion, export, and rebuild methods implemented |
| CLI | Ready for pilot | JSON-first commands; source-neutral `sync run`, top-level `xlsx-read`, projection and canonical export aliases |
| REST API | Ready for pilot | Read, exact evidence, assertion explain, connector event, sync, and review endpoints; API/admin key baseline |
| MCP | Reference adapter | Requires optional dependency and client validation |
| Filesystem connector | Ready for pilot | Read-only traversal, hash, settle-time checks |
| XLSX shallow/deep | Ready for pilot | Shared-string shallow index and exact range reader |
| PDF parser | Ready for pilot | PyMuPDF; OCR is routed but not bundled |
| HWP broker | Reference adapter | Validate `kordoc`/`unhwp` on real company corpus |
| Slack connector | Reference adapter | Validate scopes, rate limits, edits/deletes, and retention |
| Apple Mail connector | Reference adapter | macOS permission and mailbox allowlist required |
| IMAP connector | Reference adapter | Validate provider-specific UID behavior |
| Public evaluation corpus | Ready | Six checksum-pinned KOGL Type 1 PDFs; 30 relevance and 6 ACL cases |
| Evaluation reports | Ready | Recall, MRR, nDCG, ACL, latency, fingerprints, Markdown scorecard, append-only evolution ledger |
| Local embedding sidecar | Validated shadow | Infinity 0.0.77, Qwen3 0.6B 1024d, pinned revisions, MPS smoke passed |
| Local reranker | Validated shadow | BGE reranker v2 M3; depth 6 measured 1095.22 ms warmed P95 and meets the two-second gate |
| pgvector | Complete shadow, disabled | PostgreSQL 18/pgvector 0.8.2, 74/74 vectors, RLS and source-hash filtering; public pilot did not beat lexical |
| Hybrid retrieval | Complete shadow | ACL-prefiltered exact vector search, RRF, bounded reranking, explicit activation command |
| Neo4j | Port only | Do not deploy before adoption gate |
| Review UI | Not included | CLI/API review workflow only |

## Explicit pilot limitations

- The default sync mode is incremental. Forced full re-extraction and destructive source reconciliation are intentionally not exposed as one-step starter commands.
- The PostgreSQL integration test is gated by `KIP_TEST_POSTGRES_URL`; CI or a local PostgreSQL service must run it before deployment.
- The API trusts identity and ACL headers only inside the starter security boundary. Production requires an identity-aware proxy or service gateway that sets these headers.
- Optional HWP parser commands, Slack scopes, Apple Mail Automation permissions, and IMAP provider behavior must be validated against the target environment.
- Neo4j remains an adoption-gate adapter stub; canonical assertions are queried from PostgreSQL.
- The current public pilot is small and lexically distinctive. Its `keep_disabled` decision must not be generalized to a private corpus without adding reviewed internal golden cases.
