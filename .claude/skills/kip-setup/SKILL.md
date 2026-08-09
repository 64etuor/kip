---
name: kip-setup
description: Use when a user asks an AI agent to install, initialize, onboard, configure, or set up a KIP deployment, especially when source-folder scope, ACL ownership, model egress, secrets, or ontology review decisions are not yet recorded.
---

# KIP Guided Setup

Make the CLI state machine the only configuration writer. Never infer missing decisions or edit generated configuration directly.

1. Run `kip setup inspect`. Read the versioned JSON envelope.
2. If `complete=false`, ask exactly one question: the sole item in `data.questions`. Include its reason and answer format without combining it with later questions.
3. Record only that response with `kip setup answer --question ID --value VALUE`.
4. Repeat inspect and answer until complete. Do not request credential values; accept only `env:`, `keychain:`, or `secret-manager:` references.
5. Run `kip setup preview`. Show source name, classification, file count, byte count, extension counts, exclusions, symlinks, and unreadable entries. Stop if scope is broader than the user intended.
6. Run `kip setup plan --output PLAN`. Summarize read-only mounts, egress, generated files, warnings, and the fingerprint. Ask for explicit approval.
7. Only after approval, run `kip setup apply --plan PLAN`, then `kip setup verify --plan PLAN`.
8. Report the receipt and every limitation. A missing evaluation dataset means installation-ready, not production-ready.

Never edit TOML, Compose YAML, setup state, plans, or secrets yourself. Never bypass a rejected root, stale fingerprint, failed read-only check, or incomplete question. Resume from `inspect` after any interruption.

Read `references/questions.md` only when the returned answer format needs clarification.
