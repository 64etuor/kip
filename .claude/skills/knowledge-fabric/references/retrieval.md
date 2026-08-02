# Retrieval workflow

## Query order

1. Exact document number, project number, email address, Slack ID, or canonical entity ID.
2. Structured filters such as source kind, project, date, or document type.
3. PostgreSQL lexical search.
4. Vocabulary and verified alias expansion.
5. Approved graph traversal.
6. Semantic search only when enabled and lexical retrieval is demonstrably weak.
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
