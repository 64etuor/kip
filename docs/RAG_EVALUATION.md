# RAG evaluation evidence

## Current decision

Keep semantic search disabled by default. The implemented Qwen3, pgvector,
hybrid, and BGE reranking paths are operational and remain available as a
complete shadow projection, but this public pilot does not prove a quality gain
over the corrected PostgreSQL lexical path.

## Reproducible pilot

- Hardware: Apple Silicon M4 Pro, arm64, 24 GB unified memory
- Runtime: CPython 3.13.13
- Corpus: six Korean government PDFs, Public Nuri Type 1
- Corpus fingerprint: `sha256:68d47873a9c605021581b1522b6b6e0fd1e486cb6ee0cf82b4633e35a76fb6f8`
- Canonical/search units: 74
- Golden set: 30 reviewed relevance questions plus six ACL-denial cases
- Embedding: Qwen3 Embedding 0.6B, revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024 dimensions
- Reranker: BGE reranker v2 M3, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`
- Vector backend: PostgreSQL 18, pgvector 0.8.2, exact cosine search
- Benchmark protocol: one untimed full-corpus warmup pass per variant
- Current code fingerprint: `sha256:b31989422bd2693026d91f7d7862f0852accd3e204e945d513f01bf90f0e8e62`

## Evolution record

| Run | Change | Variant | Recall@10 | MRR | nDCG@10 | P95 ms | ACL leaks |
|---|---|---|---:|---:|---:|---:|---:|
| `eval_20260730T105915336204Z` | Initial natural-query baseline | lexical | 0.167 | 0.167 | 0.167 | 90.11 | 0 |
| `eval_20260730T105915336204Z` | Initial semantic shadow | vector | 1.000 | 0.972 | 0.979 | 323.98 | 0 |
| `eval_20260730T105915336204Z` | Initial RRF | hybrid | 1.000 | 0.972 | 0.979 | 153.43 | 0 |
| `eval_20260730T105915336204Z` | Rerank depth 20 | reranked | 1.000 | 0.986 | 0.990 | 6437.37 | 0 |
| `eval_20260730T110400722276Z` | Korean n-gram OR query fix | lexical | 1.000 | 1.000 | 1.000 | 49.04 | 0 |
| `eval_20260730T110400722276Z` | Corrected baseline plus RRF | hybrid | 1.000 | 0.986 | 0.990 | 87.06 | 0 |
| `eval_20260730T110400722276Z` | Corrected baseline, depth 20 | reranked | 1.000 | 0.986 | 0.990 | 6622.12 | 0 |
| `eval_20260730T110743478291Z` | Rerank depth 6 | reranked | 1.000 | 0.986 | 0.990 | 1847.64 | 0 |
| `eval_20260730T111540556068Z` | Unwarmed MPS compile exposure | reranked | 1.000 | 0.986 | 0.990 | 7733.97 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | lexical | 1.000 | 1.000 | 1.000 | 19.76 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | vector | 1.000 | 0.972 | 0.979 | 82.27 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | hybrid | 1.000 | 0.986 | 0.990 | 79.33 | 0 |
| `eval_20260730T111926992603Z` | Final warmed scorecard | reranked | 1.000 | 0.986 | 0.990 | 1095.22 | 0 |

The first run exposed an AND-query failure: short keywords worked, while full
Korean questions returned no lexical candidates. The PostgreSQL adapter now
builds a safe OR websearch query from analyzed Korean n-grams. A real
PostgreSQL regression test protects this behavior.

Reducing the rerank depth from 20 to 6 preserved every measured rank. A later
unwarmed run exposed Apple MPS dynamic-compilation cost inside the timed
samples, so the evaluator now performs and records one untimed full-corpus
warmup pass per variant. The final warmed steady-state P95 is 1095.22 ms and
passes the two-second gate. Reranking still did not improve quality over
lexical, so the promotion decision remains `keep_disabled`.

## Commands

```bash
make fetch-corpus
./scripts/fetch_public_corpus.py --check
./scripts/kip sync run --source public-government
./scripts/semantic-server.sh run
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
make evaluate
./scripts/kip evaluate compare \
  --report evaluation/reports/public-government/latest.json \
  --baseline lexical \
  --candidate reranked
```

Use `./scripts/kip`, not a bare `python -m kip.cli`, so `.env` and the
PostgreSQL URL are loaded. Keep `public-government` and both model adapters
disabled in distributed example configuration until the operator explicitly
opts into this evaluation.

## Remaining evidence gaps

This six-document pilot is intentionally small and lexically distinctive. It
does not justify a claim that lexical retrieval will dominate on a private,
larger, or more paraphrase-heavy corpus. Before activation, extend the golden
set with explicitly allowlisted internal documents, confusing near-duplicates,
tables, document revisions, stale-source cases, and harder paraphrases. Add
HNSW only when exact pgvector latency is measured to be insufficient at the
real corpus size.
