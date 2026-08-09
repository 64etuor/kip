# ADR-022: Provider-neutral structured generation

- Status: Accepted
- Date: 2026-08-09

## Context

KIP needs optional synthesis without allowing a model response to invent source
identifiers, bypass model-egress policy, or couple application services to a
vendor SDK. Remote APIs also differ in structured-output and usage envelopes.

## Decision

KIP exposes a synchronous `GenerationPort` whose request contains only exact,
reopened evidence units and whose result is a strict list of claims. Every
supported claim names one or more evidence IDs. The adapter validates that all
returned IDs were present in the request and enforces the request's claim limit.

The reference adapters use direct HTTP wire contracts for OpenAI-compatible
Responses and Anthropic Messages. They use explicit connect/read/write/pool
timeouts, bounded response bodies, no retries, ambient proxy discovery disabled,
redacted provider failures, provider request IDs, token accounting, and an
immutable configured model revision. OpenAI requests disable response storage.

Local OpenAI-compatible endpoints use the same port but must resolve to a
loopback URL. Remote adapters are constructed only when centralized egress
configuration permits remote use and an opaque secret reference resolves.

## Consequences

- Domain and application code remain independent of vendor SDKs.
- Provider output cannot cite evidence it did not receive.
- Retry and fallback policy stays in the application layer, where idempotency and
  user-visible refusal semantics can be decided explicitly.
- Environment secret references work in the reference runtime. Other secret
  schemes fail closed until a deployment-specific resolver is installed.
- Structured generation alone does not make an answer trustworthy; the answer
  use case must still reopen evidence, check freshness and egress, then assemble
  citations from the validated result.
