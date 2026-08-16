---
name: kip-setup
description: Use when a user asks an AI agent to install, initialize, onboard, configure, or set up a KIP deployment, especially when source-folder scope, ACL ownership, model egress, secrets, ontology profile, interaction-memory consent, or ontology review decisions are not yet recorded.
---

# KIP Guided Setup

Make the CLI state machine the only configuration writer. Never infer missing decisions or edit generated configuration directly.

0. Prerequisite: confirm `./scripts/kip setup inspect` runs at all. On a fresh clone it fails (missing `.venv`, `SyntaxError`, or import errors) until bootstrap has run. First check `python3 --version`: if it is older than 3.12, tell the user to install Python 3.12+ (for example `brew install python@3.12`, or set `KIP_PYTHON=/path/to/python3.12`) before anything else. Then run `./scripts/bootstrap.sh` and retry the inspect command.
1. Run `kip setup inspect`. Read the versioned JSON envelope.
   When `relation_mining_mode` is asked, explain that `enabled` requires an
   active generation provider and creates review candidates only; it never
   auto-promotes conditional or required relations into graph facts.
2. If `complete=false`, ask exactly one question: the sole item in `data.questions`. Include its reason and answer format without combining it with later questions.
3. Record only that response with `kip setup answer --question ID --value VALUE`.
4. Repeat inspect and answer until complete. Do not request credential values; accept only secret references. The runtime resolves `env:NAME` references everywhere and `file:/absolute/path` references only where the question's answer format allows it (the model credential). The database URL and bootstrap identity keys accept `env:` only. `keychain:` and `secret-manager:` references are rejected by the CLI because no runtime resolver exists.
5. Run `kip setup preview`. Show each source's name, classification, ACL scope, file count, byte count, extension counts, exclusions, symlinks, and unreadable entries. Stop if scope is broader than the user intended.
6. Run `kip setup plan --output PLAN`. Summarize read-only mounts, egress, selected ontology profile, interaction-memory consent, generated files (including the MCP runtime config), warnings, and the fingerprint. Ask for explicit approval.
7. Only after approval, run `kip setup apply --plan PLAN`, then `kip setup verify --plan PLAN`.
8. Report the receipt, every limitation, and every failed `runtime_readiness` item with its remediation. Setup is configuration-only: nothing is indexed or served until the receipt's `next_steps` have run. Walk the user through them in order: `./scripts/migrate.sh`, then `./scripts/app-up.sh` (it layers `compose.generated.yaml` over `compose.yaml`), then `./scripts/kip sync run --source SOURCE`, then a search smoke test. A missing evaluation dataset means installation-ready, not production-ready. The recorded `sync_schedule` is declarative documentation only; nothing schedules syncs automatically, and the launchd installer configures its own interval.

Never edit TOML, Compose YAML, `.mcp.json`, setup state, plans, or secrets yourself. Never bypass a rejected root, stale fingerprint, failed read-only check, or incomplete question. A clarification answer is not memory unless the interaction service records explicit confirmation, and an ontology discovery candidate never activates a YAML contract. Resume from `inspect` after any interruption.

Read `references/questions.md` only when the returned answer format needs clarification.
