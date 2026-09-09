---
name: kip-setup
description: Install or configure a KIP deployment through its guided setup state machine, including source scope, identity, model egress, and consent decisions.
---

# KIP Guided Setup

Run from the KIP repository with `./scripts/kip`. On a fresh clone, use
`./scripts/bootstrap.sh` first; it requires Python 3.12+ and supports
`KIP_PYTHON=/path/to/python3.12`. Resume interrupted setup from inspect.

1. Run `./scripts/kip setup inspect`. If incomplete, ask exactly one question:
   the returned `data.questions` item, with its reason and answer format.
2. Record that response using `./scripts/kip setup answer --question ID --value VALUE`.
   Repeat inspect. Accept secret references, never credential values:
   `env:NAME`, or `file:/absolute/path` only for model credentials.
   `keychain:` and `secret-manager:` are rejected. Enabled relation mining
   requires a generation provider and creates candidates, not approved facts.
3. Run `./scripts/kip setup preview` and check source scope against the request.
   Run `./scripts/kip setup plan --output PLAN`, show its generated files,
   read-only mounts, egress, ontology, consent, warnings, and fingerprint.
   Obtain approval of this concrete plan.
4. Run `./scripts/kip setup apply --plan PLAN`, then
   `./scripts/kip setup verify --plan PLAN`. Report the receipt's limitations
   and failed `runtime_readiness` items with their remediation.

Setup is configuration-only. Follow the receipt's `next_steps` within the
authorized setup: migrate, `./scripts/app-up.sh`, source sync, then search and
exact-read smoke. `app-up.sh` loads the generated Compose override.
`sync_schedule` is declarative metadata; a scheduler must be installed separately.
A missing evaluation dataset means installation-ready, not production-ready.

Never edit TOML, Compose, `.mcp.json`, setup state, or plans to bypass the state
machine. A rejected root, stale fingerprint, or failed read-only check needs
resolution before apply. Clarification is not consent to remember a preference.
Ontology discovery approval is a separate admin decision and can automatically
write an additive YAML release (ADR-044); setup does not authorize that review.

Read [question formats](references/questions.md) only when the returned answer
format needs clarification.
