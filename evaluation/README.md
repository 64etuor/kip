# End-to-end RAG Evaluation

This directory contains reproducible retrieval, answer, and ontology quality
inputs and generated evidence.

## Source-controlled inputs

- `schemas/`: JSON Schema contracts for golden cases and reports.
- `golden/`: reviewed question sets with stable expected documents and locators.
- `reviews/`: version-bound human observations of generated answers and graph output.
- `corpus/public-government.json`: explicit, licensed public-document acquisition allowlist.
- `corpus/README.md`: fetch, checksum, attribution, and redistribution rules.

## Generated outputs

- `reports/public-government/<run-id>.json`: complete machine-readable run evidence.
- `reports/public-government/<run-id>.md`: human-readable scorecard.
- `reports/public-government/latest.json` and `latest.md`: atomic convenience copies.
- `reports/jina-reranker-v2/`: pinned Jina Hugging Face trial report and gate decision.
- `reports/evolution.jsonl`: append-only redacted comparison history.

Reports must not contain source bodies, secrets, model credentials, or private filesystem paths.
The evaluator records document IDs, unit IDs, locators, hashes, metrics, and bounded error messages.
Reviewer identities are intentionally omitted from reports.

Semantic candidates remain shadow-only until the activation gates in
`docs/plans/2026-07-30-rag-quality-stack-design.md` pass. An evaluator recommendation never
changes active configuration automatically.

`golden/private-starter.yaml` is a redacted acceptance template. It covers
exact evidence, Korean paraphrase, XLSX original-range requirements, weak-query
refusal, ACL denial, and missing/stale sources. A real deployment must replace
or extend it with reviewed internal cases and must not commit sensitive source
bodies or private paths.

`golden/ontology-starter.yaml` and `reviews/ontology-starter.yaml` are a
synthetic contract pair. They exercise strict lifecycle, citation, refusal,
graph, temporal, integrity, and ACL gates but are not deployment evidence.
Copy them into a private, access-controlled evaluation location and replace all
expectations with reviewed current evidence.

The checked-in public pilot contains 30 relevance cases and six ACL-denial
cases. Run it only through `./scripts/kip`, which loads `.env`; invoking
`python -m kip.cli` directly can select the non-durable memory repository when
`KIP_DATABASE_URL` is absent.

`evaluate run` defaults to one untimed full-dataset warmup pass per variant and
records that count in `run.warmup_passes`. This keeps persistent-sidecar
steady-state latency separate from model loading and Apple MPS graph
compilation. Use `--warmup-passes 0` only when deliberately measuring a cold
path.
