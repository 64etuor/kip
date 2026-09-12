# Architecture decision records

Each file records one architecture decision, its context, and its
consequences. An ADR is history: once accepted it is amended or superseded,
never rewritten to match current behavior. For current behavior read
[`../PRD.md`](../PRD.md), [`../TRD.md`](../TRD.md) and
[`../IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md).

## Header convention

Every ADR starts with an H1 naming its number and decision, then one metadata
list. No other shape is valid — not a `## Status` heading, not a bare
`Status: …` paragraph, not an inline date on the status line.

```markdown
# ADR-0NN: Short decision title

- **Status:** Accepted
- **Date:** 2026-08-13
```

Fields, in this order:

| Field | Required | Meaning |
| --- | --- | --- |
| `**Status:**` | yes | One value from the closed vocabulary below. |
| `**Date:**` | yes | The decision date, `YYYY-MM-DD`. |
| `**Superseded by:**` | when `Superseded` | `ADR-0NN (YYYY-MM-DD) — what replaced it`. |
| `**Amended by:**` | when `Amended` | `ADR-0NN (YYYY-MM-DD) — what changed`, or a release number when no ADR carried the change. Repeat the field once per amendment, oldest first. |

Anything else (`**Decision owners:**`, `**Scope:**`, `**Re-test trigger:**`)
is optional prose and carries no contract.

## Status vocabulary

These five values are the whole vocabulary. A reader must not have to
interpret a qualifier appended to the status.

| Status | Meaning |
| --- | --- |
| `Proposed` | Written, not yet decided. Not a basis for implementation. |
| `Accepted` | Decided and still the standing decision in full. |
| `Amended` | Still standing, but a later decision changed part of it. Every change is listed in an `**Amended by:**` field, and the body keeps the original text so the history stays readable. |
| `Superseded` | No longer the standing decision. `**Superseded by:**` names its replacement. Read it for history only. |
| `Rejected` | Considered and declined. Kept so the option is not re-proposed without new evidence. |

Amend the status line rather than editing the body of an ADR that a later
decision changed. When an amendment needs explanation beyond one line, add a
blockquote directly under the metadata list; leave the original decision text
below it untouched.

## Numbering

ADR numbers are allocated in order and never reused. **ADR-006 through
ADR-016 do not exist.** No file was ever committed under those numbers — the
range was skipped, not deleted — so the gap is not evidence of a missing or
withdrawn decision. Do not fill it; allocate the next unused number above the
highest existing ADR.

## Decisions that outrank an ADR

An ADR cannot weaken the architecture invariants in
[`../TRD.md`](../TRD.md) section 2 or the traps in the repository root
[`AGENTS.md`](../../AGENTS.md). A change to either needs an ADR *and* a
matching edit to those documents in the same change; cite the invariant by
its text, not by a list position.
