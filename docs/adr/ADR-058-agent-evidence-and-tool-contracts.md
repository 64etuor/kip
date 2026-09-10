# ADR-058: Evidence completeness and discoverable tool contracts

- Status: Accepted
- Date: 2026-09-10

## Decision

Question-language heuristics are not a reliable boundary for incomplete table
evidence. Shallow XLSX units remain available for discovery, but cannot enter
the final-answer evidence set. A refusal carries the sheet/artifact locator for
an exact range read. Other complete evidence can still answer. CSV chunks
qualify together only when artifact, extraction, source hash, and contiguous
nonoverlapping row coverage establish a complete table, and `max_chars` can
carry that table without truncation.

Ontology context whose backing evidence was excluded is omitted instead of
leaving missing references in the response. Source, ACL, freshness, and egress
policies remain unchanged.

MCP discovery carries purpose, mutation hints, and canonical input bounds.
Application models enforce those bounds inside the KIP envelope boundary.
Unexpected handler errors expose generic typed errors without internal details.
Protocol-level argument-shape errors remain SDK errors. The server accepts an
injected container, like REST, for tests independent of private deployment data.

## Consequences

No model is needed to classify numeric intent. Workbook metadata or incomplete
CSV evidence alone can now refuse a non-numeric question too; agents can still
inspect it with search/read tools. Complete CSV evidence needs enough context
budget. This does not establish semantic entailment for arbitrary document
answers. Native agent observations are recorded in `docs/AGENT_QUALITY.md`.
