---
name: kip-setup
description: Install or configure a KIP deployment through its guided setup state machine, including source scope, identity, model egress, and consent decisions.
---

# KIP Guided Setup

Run the state machine through the deployment's `kip` wrapper. The steps write
`./scripts/kip`, which works in the deployment directory. Elsewhere use the
global `kip` launcher an installed deployment writes,
`"$KIP_PROJECT_DIR/scripts/kip"`, or the sibling `knowledge-fabric` skill's
`scripts/kip.sh`, which resolves the deployment named in this skill's
`.kip-skill-install`. `kip setup` acts on the deployment whose `scripts/kip`,
launcher or `kip.sh` you ran (an exported `KIP_PROJECT_ROOT` overrides it);
pass `--project-root DIR` to target another directory. Run other
`./scripts/*.sh` in the deployment directory. On a fresh clone run `./scripts/bootstrap.sh` first.
Resume an interrupted configuration from inspect.

1. Run `./scripts/kip setup inspect`. If incomplete, ask exactly one question:
   the returned `data.questions` item. Each question carries `prompt`,
   `answer_format`, `why` (the reason to relay — there is no `reason` field),
   and optionally `choices` and `example`.
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

## After apply

Setup is configuration-only. Never start or stop another deployment's stack.
When bootstrap creates `.env` it keeps exported `KIP_POSTGRES_PORT` and
`KIP_API_PORT` (even if in use) and an exported `COMPOSE_PROJECT_NAME`. Without
an exported project name it checks the `kip` project and the PostgreSQL and API
ports; if another directory's containers, unattributed volumes, or a busy port
collide, it chooses a project name and free ports and prints them. Only confirm
the printed values with the user; if it mentions volumes that may be this
deployment's own, ask whether `.env` should be restored instead. If it stops
because this directory's containers exist but `.env` is missing, ask the user to
restore `.env` from backup. An existing `.env` keeps its project name and
ports: if `runtime_readiness` fails `compose_project_isolation` or
`app-up.sh` refuses naming another directory, set those values (and the same
port in `KIP_DATABASE_URL` and `KIP_BACKUP_DATABASE_URL`) with consent. Keep
one model runtime per machine.
Then follow the receipt's `next_steps` within the
authorized setup: `./scripts/app-up.sh --database-only` (database readiness and
migration for CLI/MCP; only the database credential is required and no
API/worker image is built; it also starts an installed model runtime), source
sync, then search and exact-read smoke. The first sync embeds the corpus; until
that projection is active, which can take hours on a large corpus, search
returns lexical results with a `semantic_degraded` warning, and `kip doctor`'s
`semantic_search` check gives the fix command. Run
`./scripts/app-up.sh` only when the API and worker are needed; it runs one
model runtime per machine, so on ARM or beside a running host runtime the
containers search lexically (`semantic_degraded`). Both select the
standalone generated Compose. All-cloud sources need user-selected local
downloads; a local generation choice still needs a separately verified service.
`sync_schedule` is declarative metadata; a scheduler must be installed separately.
A missing evaluation dataset means installation-ready, not production-ready.

## Connect agents

Setup does not connect the agent the user works in. After the smoke search,
offer these steps and run them only with consent, because they write the
user's client configuration:

1. Register MCP by the launcher's absolute path (a running client does not see
   the PATH the installer added): `claude mcp add --scope user kip -- KIP mcp`
   or `codex mcp add kip -- KIP mcp`, where `KIP` is the launcher path the
   installer printed (default `~/.local/bin/kip`, expanded). Without a launcher
   use `DEPLOYMENT/scripts/kip mcp`.
2. Install skills: `DEPLOYMENT/scripts/install-agent-files.sh personal --client all`,
   or `project DIR` for one project.
3. Tell the user to restart the client; `claude mcp get kip` or
   `codex mcp get kip` shows the stored entry.

## Never bypass the state machine

Never edit TOML, Compose, `.mcp.json`, setup state, or plans to bypass the state
machine. A rejected root, stale fingerprint, or failed read-only check needs
resolution before apply. An action-required exit is not a successful setup:
explain the next step and rerun after it is completed. Native administrator
authentication and Desktop license/first-run choices belong to the user's
terminal/UI, never chat secrets. Clarification is not consent to remember a
preference. Ontology discovery approval is a separate admin decision and can
automatically write an additive YAML release (ADR-044); setup does not authorize
that review.

## Environment caveats

Bootstrap prepares Python 3.12+ and Node/npm locally when missing, without
changing system runtimes or shell profiles. Use `--check` for read-only
prerequisite status. System Docker installation uses `--install-docker`; explain
that action and obtain authorization if the request has not already provided it.
Use `--without-docker` only for an external-DB CLI/MCP installation. Explicit
`KIP_PYTHON` remains authoritative; a broken or old existing `.venv` is preserved
for the user to move aside.

A recipient without the repository can instead run the published installer
(`curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh
| bash`), which verifies the release archive before extracting into an empty
directory and then runs bootstrap; `./scripts/upgrade.sh --latest` updates an
existing package deployment in place. That installer also writes a global `kip`
launcher and a marked block in the login shell profile, so an installed
deployment runs `kip setup inspect` and `kip update` (the same upgrade) from any
directory after the shell is restarted; a repository checkout keeps using
`./scripts/kip`.

Semantic search (`hybrid`) is the default and the BGE reranker is opt-in
(ADR-065): bootstrap also installs an isolated model runtime and the pinned
embedding model (about 1.2 GB) unless
`KIP_SEMANTIC=off` is set or the host has less than 8 GiB of RAM. A failure
there leaves bootstrap successful and search lexical. The plan's
`semantic_search` records whether the runtime is installed; a lexical-only plan
drops the compose `models` service and warns why. Report that choice with the
plan rather than changing it silently.

Read [question formats](references/questions.md) only when the returned answer
format needs clarification.
