# ADR-065: Semantic search on by default

Status: Accepted — 2026-09-12

## Context

Since ADR-005 and ADR-035/037, vector retrieval was implemented but off by
default. Turning it on required a separate model runtime, a manual projection
rebuild, an evaluated activation and a configuration change, and no sync kept
the projection current. On the reviewed private corpus the historical measure
already favoured semantic retrieval for paraphrased questions, but the release
never shipped it as the default path.

Measuring the default path on a real corpus also exposed operational gaps that
a default must not have:

- The lexical channel, which hybrid and reranked modes also run, took several
  seconds per query on a corpus of about 100k units. Shared folder names are
  added to every unit's lexical text, so an n-gram such as `절차` matched half of
  the corpus; each match paid the ACL and freshness checks. The abstention gate
  counted every document for every query term on each search.
- A model runtime that is down or hung made every query wait for a failure.
- When only the reranker failed, reranked mode threw away the vector channel.
- Neither the portable gate nor the private gate measured a semantic mode.
- The model runtime's dependencies were pinned only at the top level.

## Decision

- Default configuration: `search.semantic_enabled = true`, default mode
  `hybrid` and the pinned embedding (Qwen3-Embedding-0.6B at a fixed
  revision, 1024 dimensions, 4000-character head/tail projection; 12000
  drove the runtime to a 15 GB footprint on a 24 GB Mac).
  `SEMANTIC_DEFAULT_MODE` and `EMBEDDING_DEFAULTS` in
  `kip.application.semantic` are the single source for setup, the example and
  container configs and the portable gate.
- Reranking: the BM25 reranker stays on for lexical mode and for the lexical
  fallback, also when a cross-encoder is enabled (`[models.lexical_reranker]`,
  BM25 by default), so the fallback never depends on the model runtime. The
  BGE reranker-v2-m3 cross-encoder is opt-in
  (`models.reranker.backend = "http"`, optionally
  `search.default_mode = "reranked"`, and a runtime started with
  `KIP_SEMANTIC_RERANKER=on`). The default runtime and the compose `models`
  service load only the embedding model; production `models-fetch` downloads
  both, so enabling the reranker later works offline.
- Installation: bootstrap installs the isolated model runtime from the
  hash-locked `requirements/semantic.txt` and prefetches the pinned model
  snapshots unless `KIP_SEMANTIC=off` or the machine has less than 8 GiB of
  memory. Failure never fails bootstrap. Setup records whether the runtime is
  present (`SetupPlan.semantic_search`) and generates matching configs.
  `app-up.sh` starts exactly one runtime per machine (the compose `models`
  service, profile `semantic`, on amd64; the host runtime otherwise), and a
  supervised `semantic-server.sh run` waits instead of loading models while
  another runtime owns the port;
  `install-launchd.sh` supervises it;
  `deploy/systemd/kip-semantic.service` covers Linux hosts; compose adds a
  digest-pinned `models` service reached through
  `security.model_service_hosts` without enabling remote egress. Production
  compose serves it offline on an internal network and fetches models once
  through a separate `models-fetch` profile.
- Projection maintenance: every sync, and every activated re-extraction, embeds
  new or changed units. A complete projection whose embedding identity is in
  `RELEASE_REVIEWED_EMBEDDING_IDENTITIES` activates automatically
  (`search.semantic_auto_activate`, default on). Any other identity is
  completed but waits for evaluation and `kip projection activate`, so the
  ADR-036/037 governance still applies to custom models.
- Degradation: default-mode search falls back to the lexical path with a
  `semantic_degraded` warning when the runtime or the active projection is not
  available, and keeps the fused lexical+vector ranking with
  `rerank_degraded` when only the reranker fails. A per-adapter circuit
  (`models.circuit_cooldown_seconds`) skips a failed runtime for a cooldown;
  query embeddings use their own short timeout. Explicit mode requests still
  fail instead of degrading.
- Lexical performance: query n-grams present in at least
  `search.lexical_common_term_fraction` of lexical units are left out of
  candidate matching (the BM25 reranker still scores the full question), and
  the abstention gate uses an existence check.
- Quality gates: the portable gate runs lexical and the shipped default mode
  (with a deterministic hashing embedding) at recall and MRR 1.0 with zero ACL
  leaks; the private gate checks per-variant floors for the default mode and
  for the lexical fallback and fails when the corpus is present but the
  configured semantic path is not, or when the deployment's configured default
  mode has no floor.
- Supply chain: `scripts/audit-semantic.sh` audits the runtime lock in
  `verify.sh`; reviewed advisories are listed with the reason they do not
  apply and any new one fails.

## Evidence

Private reviewed set: 19 cases over the onedrive-personal source (1,912 files,
176,545 units), space `qwen3-embedding-0.6b-1024-c4000-ht1`, float16 runtime
on the reference Apple Silicon Mac, one warmup pass. All variants had zero
failed cases and zero unauthorized results.

| Variant | Recall@10 | MRR | nDCG@10 | P50 | P95 |
|---|---:|---:|---:|---:|---:|
| lexical (BM25 rerank, common-term pruning) | 89.5% | 63.8% | 70.2% | 1.45 s | 2.22 s |
| vector | 89.5% | 83.3% | 84.9% | 0.06 s | 0.08 s |
| hybrid (default) | 89.5% | 89.5% | 89.5% | 1.64 s | 2.42 s |
| reranked, BM25 | 78.9% | 59.8% | 64.5% | 1.49 s | 2.33 s |
| reranked, BGE, 40 candidates | 89.5% | 83.3% | 84.9% | 6.87 s | 16.18 s |
| reranked, BGE, 20 candidates | 94.7% | 85.3% | 87.6% | 5.32 s | 7.20 s |

Before common-term pruning the lexical row was 78.9% / 58.3% / 63.6% with a
P95 of 11.1 s. The public government set (36 cases, six PDFs) is saturated:
lexical, hybrid and both reranked variants reached recall 100% and MRR 98.6%,
vector 97.2%; P95 was 0.04-0.10 s except BGE at 6.6 s.

Hybrid is the default because it had the best MRR and nDCG at a latency close
to lexical. BM25 reranking on top of the fused list reordered good vector hits
below lexical matches and fell under lexical quality. The BGE cross-encoder
found one more case with 20 candidates but ranked below hybrid and cost 3-4x
the latency, and it needs about 2 GB more runtime memory; with both models
loaded and 12000-character inputs the runtime had grown to 10-15 GB. The set is
small (one case moves recall by 5.3 points), so the private gate records floors
(hybrid recall and MRR 0.84, lexical recall 0.84 and MRR 0.60, P95 at most
8 s) rather than exact scores.

## Consequences

New installs download about 1.2 GB for the pinned embedding model (the
reranker adds about 2.3 GB when enabled) and embed the corpus after the first
sync; embedding throughput on the reference machine ranges from
about 100 short units to roughly 2,800-5,000 characters per second for longer
units, so a first full projection of a large corpus takes hours (about three
for a 1,912-file, 176k-unit OneDrive corpus) and search stays lexical until
it is complete. Lexical-only machines keep working with `KIP_SEMANTIC=off`.
Infinity 0.0.77 is the latest release and bounds transformers below 5; its
reviewed advisories are recorded in `docs/SECURITY.md`, and replacing the
runtime is tracked as a limitation. The published Infinity image is amd64 only,
so ARM hosts run the native runtime and their API/worker containers search
lexically. A deployment that accepts the latency can enable the BGE reranker
and should re-run the private gate with its own floors.
