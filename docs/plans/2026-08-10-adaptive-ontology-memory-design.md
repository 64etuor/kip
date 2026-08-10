# Adaptive Ontology and Consent-Based Memory Design

**Status:** Approved
**Date:** 2026-08-10

## Decision

KIP keeps a small, stable ontology kernel and starts new starter-kit deployments
with an empty domain profile. It does not start with an empty ontology contract:
the kernel continues to define the generic entity and predicate constraints that
make evidence-backed relation review possible.

User interaction is split into three independent control planes:

1. A bounded clarification exchange resolves an ambiguity that would materially
   alter source scope, interpretation, or retrieval.
2. A principal-scoped preference is persisted only after an explicit `remember`
   confirmation. It is editable and deletable by that same principal.
3. An ontology discovery candidate is created only from an explicitly confirmed
   user proposal. It is not an assertion, graph edge, YAML edit, or active
   ontology change. A reviewer may accept it for a future ontology release, but
   validation, diff, migration coverage, and release activation remain separate.

This adopts the useful parts of agent-memory systems: structured questions,
bounded choices, explicit persistence, and separate procedural/domain memory.
It intentionally excludes automatic global-memory writes, free-form source
content as memory, and model-weight training.

## Goals

- New starter-kit configuration selects `empty` unless the adopter deliberately
  chooses `research-project`.
- An AI client can ask one structured clarification through the same KIP
  application service used by CLI, REST, and MCP.
- A user can save, edit, list, or delete only their own durable preferences.
- Helpful/unhelpful feedback is recorded as a compact, redacted signal rather
  than as an answer, query, source body, or principal-bearing query trace.
- Repeated explicit domain proposals are deduplicated into a reviewer queue.
- PostgreSQL remains canonical for durable interaction state; in-memory storage
  implements the same contract for tests and offline smoke checks.

## Non-goals

- Automatically infer or activate a new ontology type, predicate, controlled
  value, or alias from a search result.
- Use a query trace as training truth, or add raw queries, answers, source text,
  prompt text, credentials, or provider payloads to `QueryTrace`.
- Make a discovery candidate visible as an approved assertion or graph edge.
- Store a shared global user profile, or let one principal inspect another
  principal's preferences or open clarifications.
- Trigger a sync, indexing run, embedding build, graph rebuild, or model call
  while handling a clarification, preference, feedback, or discovery proposal.

## Ontology profiles

```text
ontology/core/*                 stable generic kernel
ontology/domains/empty.yaml     no domain symbols or controlled values
ontology/domains/research-project.yaml
                                optional demonstrator profile
```

`OntologyCatalog` validates the selected profile together with the core files.
The existing `core/<version>` public ontology version stays stable so existing
approved assertions and migration manifests do not change merely because this
starter-kit profile is introduced. The selected profile is configuration, not a
database enum or vendor label.

Setup asks for `ontology_profile` and writes it to generated configuration.
The distributed example configuration chooses `empty`; compatibility callers
that omit the setting retain the established `research-project` profile.

## Interaction data flow

```text
agent detects material ambiguity
        |
        v
create clarification (max 4 choices; optional free text)
        |
        v
user answers
   |                 \
   | remember=false   \ remember=true + preference key
   v                  v
redacted event     principal preference upsert
        |
        v
optional explicit domain proposal
        |
        v
ontology discovery candidate -> reviewer decision -> future YAML release flow
```

Clarification questions are short-lived and principal-scoped. Their persistent
shape contains the question needed to complete a cross-surface exchange, but
not source bodies, evidence excerpts, or a raw query field. Feedback consists
only of a safe request ID when available, a bounded outcome, and bounded reason
codes. The event payload has no free-text answer, query, answer, or source body.

Preferences are a constrained key plus a bounded list of values. They are
direct user settings, not facts about the corpus, and therefore never flow into
the assertion or graph tables. Discovery proposal fields are stored only after
the user elects to propose that terminology. The candidate carries its explicit
origin and occurrence count, but no authority to mutate the active catalog.

## Authorization and retention

- Interaction memory is disabled until setup selects `explicit_consent`.
- PostgreSQL sets a trusted current-principal session value for the interaction
  adapter. RLS limits preferences, clarifications, and feedback to that
  principal and workspace.
- Discovery review/list operations require the existing verified `admin` role.
  Candidate write/read paths remain workspace-isolated; application code never
  treats a candidate as a fact.
- Expired clarification records are unusable. A bounded admin pruning operation
  removes only expired records; normal retrieval remains side-effect free.
- Preference deletion is ownership-scoped. There is no implicit re-learning
  after deletion.

## Edge contract

Every surface calls `InteractionUseCases`:

| Need | CLI | REST | MCP |
| --- | --- | --- | --- |
| Ask / answer clarification | `interaction clarify`, `interaction answer` | `/v1/interactions/clarifications` | `kip_clarify`, `kip_clarification_answer` |
| Manage own preferences | `interaction preferences ...` | `/v1/interactions/preferences` | `kip_preferences`, `kip_remember`, `kip_forget` |
| Send feedback | `interaction feedback` | `/v1/interactions/feedback` | `kip_feedback` |
| Propose / review domain term | `ontology discovery ...` | `/v1/ontology/discovery-candidates` | `kip_ontology_discovery_*` |

All successful calls emit `kip.envelope.v1` at CLI and REST. MCP serializes the
same typed application result. The generated schema set contains the new public
models and OpenAPI documents.

## Verification

- Core plus `empty` and `research-project` profiles validate; a missing profile
  fails closed.
- Setup still asks one question at a time and writes the chosen profile and
  explicit-consent interaction policy.
- Memory and PostgreSQL implementations enforce preference ownership,
  clarification expiry, explicit memory consent, feedback redaction, discovery
  idempotency, and review-only activation semantics.
- CLI, REST, and MCP tests prove the same application outcomes for a real
  clarification and preference flow.
- Query trace tests prove interaction data did not weaken the closed redacted
  trace schema.
- `./scripts/verify.sh` and a manual CLI scenario pass before the implementation
  is committed.
