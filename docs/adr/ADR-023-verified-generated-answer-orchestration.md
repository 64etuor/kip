# ADR-023: Verified generated-answer orchestration

- Status: Accepted
- Date: 2026-08-09

## Context

Search snippets are discovery aids and model-generated citations are untrusted.
Connecting a generator directly to ranked hits would allow stale content,
unauthorized source identifiers, or fabricated citations to appear as answers.

## Decision

The shared answering application service performs one ordered pipeline:

1. retrieve with ACL filtering;
2. reopen each exact content unit through the evidence service;
3. remove stale source revisions;
4. apply relevance, approval-claim, and exact-XLSX gates;
5. evaluate the centralized model-egress policy over canonical classifications;
6. send only admitted, bounded, reopened evidence to the configured generator;
7. validate model identity, revision, claim count, and every evidence ID again;
8. assemble public citations from the reopened evidence objects.

CLI, REST, MCP, and the Python client route to this same application contract.
Disabled generation retains the deterministic extractive response. Provider or
contract failure returns a typed refusal by default. Extractive fallback occurs
only when `models.generation.fallback_on_error = true`; the response carries a
machine-readable warning.

## Consequences

- No model sees raw search-hit snippets or evidence denied by egress policy.
- A generated answer cannot cite an unknown or non-admitted content unit.
- Stale evidence and spreadsheet numeric intent fail before model invocation.
- Provider failures do not silently change answer semantics.
- The additive `kip.answer.v1` fields expose claims, model revision, token usage,
  provider request ID, and the egress decision without revealing credentials.
