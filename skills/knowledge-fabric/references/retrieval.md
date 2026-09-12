# Retrieval workflow

## Query order

1. Exact document number, project number, email address, Slack ID, or canonical entity ID.
2. Supported filters such as source kind, project, or document type. Date-range
   filtering is not in the current search contract; inspect dates in exact evidence.
3. PostgreSQL lexical search.
4. Vocabulary and verified alias expansion.
5. Approved graph traversal.
6. Default search (`hybrid`) already fuses vector and lexical candidates when the deployment's semantic projection is active, and reranks them only where the deployment chose `reranked`; any `meta.warnings` entry ending in `_degraded` means that ranking fell back, so treat the result as weaker. Request an explicit `vector` or `hybrid` mode only to diagnose, not to answer.
7. Exact source read.

## Weak result signals

Treat a result set as weak when it has no exact identifier match, low score separation, repeated duplicate documents, stale sources, or no readable evidence locator. Use `vocab` and a narrower query rather than inventing unsupported synonyms.

## Context packs

Use `context` to limit total characters and diversify documents. The context pack is not a final citation. Read the selected unit before asserting dates, amounts, approval, supersession, or obligations.

## Answer discipline

For each material claim report:

- logical document or message title;
- source kind;
- page, section, cell range, Slack message timestamp, or Message-ID;
- indexed source hash;
- whether the original changed after indexing.
