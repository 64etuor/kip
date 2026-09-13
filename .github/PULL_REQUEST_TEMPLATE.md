<!-- Korean or English is fine. Delete any section that does not apply. -->

## What this changes, and why

## Evidence

`./scripts/verify.sh` is the completion gate — tests, lint, typecheck and the
dependency audit, with a missing check tool counting as a failure rather than a
skip. Paste its final line, or say why it could not run here.

Commands or tests that demonstrate the new behaviour:

## Before merge

The rules behind these live in [`CONTRIBUTING.md`](../CONTRIBUTING.md) and
[`AGENTS.md`](../AGENTS.md); this list is only a reminder of which ones this
change trips.

- [ ] An adapter changed, and the contract test came first.
- [ ] The schema changed, and this adds a **new** migration. No existing
      migration file was edited — they are append-only once deployed.
- [ ] An MCP name, argument or envelope changed, and
      `tests/test_mcp_envelope_contract.py` changed with it.
- [ ] OpenAPI was regenerated with `./scripts/generate-contracts.sh`.
- [ ] `skills/` was edited and copied byte-for-byte into `.claude/skills/`.
- [ ] Behaviour, contract, configuration or deployment changed, so `CHANGELOG.md`
      and the document that owns the area were updated in this PR.
- [ ] This is an architecture decision, so it carries an ADR in `docs/adr/` plus
      the parent documents it changes.
- [ ] Public IDs and `kip.envelope.v1` field meanings are preserved — or the
      break is named above with what a client sees instead.
