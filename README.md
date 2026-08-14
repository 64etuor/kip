# KIP Knowledge Fabric Starter Kit v3.3

KIP is an agent-first, evidence-first foundation for indexing and retrieving company knowledge from NAS files, HWP/HWPX, PDF, PPTX, XLSX, Slack, and email. It supports two equal entry paths:

- **Agent/terminal path:** stable CLI commands that emit versioned JSON.
- **Application path:** REST/OpenAPI and optional MCP adapters backed by the same services.

The baseline runtime is PostgreSQL 18 with PostgreSQL-native lexical search. `pgvector` is installed by the reference image but semantic search remains disabled until an evaluation proves value. Neo4j is not canonical and is only a future read projection.

The approved target, current implementation, measured evidence, and remaining
production gaps are separated in
[`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md).
Do not infer current readiness from PRD/TRD target language alone.

## 1. Repository guarantees

- `AGENTS.md` and `CLAUDE.md` are at the project root.
- `CLAUDE.md` imports `AGENTS.md` with `@AGENTS.md`.
- NAS and connector sources are read-only.
- HWP/HWPX uses the measured native `hwp-hwpx-parser` adapter first, with replaceable command and paired-PDF fallbacks.
- XLSX uses shallow-all / deep-candidate retrieval.
- PPTX preserves slide/shape structure, tables, chart caches, image metadata,
  notes, comments, SmartArt text, and exact geometry without executing macros or
  fetching external relationships.
- CLI, REST, and MCP use the same application layer.
- Search, graph, and embeddings are replaceable projections.

## 2. Agent-guided setup

On a new deployment, do not hand-edit configuration files by guesswork.
Instead, give an AI agent a single instruction:

```text
KIP을 셋업해줘   (set up KIP)
```

Following `skills/kip-setup/SKILL.md`, the agent asks the questions returned by
`kip setup inspect` strictly one at a time. It settles the organization
workspace, identity verification, the absolute path of every folder to index
with its extension/exclusion scope, classification, and ACL, the model egress
policy, secret references, CAS/backup paths, the evaluation dataset, an empty
starter or example ontology profile, explicit interaction-memory consent, and
the ontology reviewers. It then previews file counts, sizes, extension
distribution, exclusions, and symlinks, and only after the user approves the
plan fingerprint and read-only mounts does it atomically generate and verify:

- `config/kip.generated.toml` (container paths, mounted by the compose override)
- `config/kip.host.generated.toml` (host paths, used by the MCP adapter)
- `compose.generated.yaml`
- `.mcp.json` pointing at the generated host runtime configuration

Raw credentials never enter questions, state, plans, or generated files. Only
`env:` references the runtime can resolve are recorded (`file:` is also allowed
for the model credential); `keychain:`/`secret-manager:` are rejected. When no
evaluation dataset exists, the receipt records that the install is usable but
not production-promoted.

`setup apply`/`verify` only generate configuration. The deployment actually
runs after `./scripts/migrate.sh`, `./scripts/app-up.sh` (which layers the
generated compose override on the base file), `./scripts/kip sync run --source
SOURCE`, and a search smoke test. The full acceptance procedure is in
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md).

## 3. Quick start - local development

When adopting this kit in another organization or repository, follow
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md) first. It bundles the
per-environment decisions, the AI change contract, real-corpus acceptance
tests, and the update-notification and promotion/rollback criteria into a
single path.

Prerequisites: Python 3.12+, Node.js 18+, and Docker with Compose. Bootstrap
installs the pinned Kordoc OCR runtime and verifies the Korean model cache;
normal indexing never downloads parser packages or models at runtime.

```bash
cp .env.example .env
cp config/kip.example.toml config/kip.toml

./scripts/bootstrap.sh
./scripts/dev-up.sh
./scripts/migrate.sh
./scripts/doctor.sh
./scripts/test.sh
```

Index the bundled sample data:

```bash
./scripts/kip sync run \
  --source sample

./scripts/kip search "참여율 변경 승인" --limit 10
./scripts/kip context "정산 증빙 제출기한" --limit 5
```

## 4. Run as an application

Start the API locally:

```bash
./scripts/api.sh
```

Then call it from another app:

```bash
curl -sS http://127.0.0.1:8080/v1/search \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -d '{"query":"협약 변경 승인","limit":5}'
```

A Python client is included at `sdk/python/kip_client.py`. OpenAPI is generated from the FastAPI app and checked into `contracts/openapi.json` during release verification.

External applications can also push normalized source changes through the connector endpoint:

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  --data-binary @examples/connector/event.json
```

At an internet boundary, arbitrary workspace, principal, and ACL headers are
never trusted. API-key bootstrap uses the single principal pinned in
configuration; multi-user deployments derive workspace and scopes from
verified JWT claims.

CLI, REST, MCP, and connector events all enter the same application service layer. Applications must not connect directly to PostgreSQL or an optional graph projection.

## 5. Claude Code and MCP

Claude Code loads root `CLAUDE.md`, which imports `AGENTS.md`. The project skill lives at:

```text
.claude/skills/knowledge-fabric/SKILL.md
```

The root `.mcp.json` starts the optional stdio MCP adapter without embedding secrets in the file. Guided setup rewrites it atomically to select the host-path `config/kip.host.generated.toml` (the container-path `config/kip.generated.toml` is for the compose services) and preserves the previous file. `./scripts/bootstrap.sh` installs the MCP and identity runtimes. For a standalone package install, select the MCP extra explicitly:

```bash
python -m pip install 'kip-knowledge-fabric[mcp]'
```

MCP is optional; the CLI remains the lowest-dependency agent interface.
Set `KIP_WORKSPACE`, `KIP_PRINCIPAL_ID`, and `KIP_ACL_SCOPES` in the environment used by the MCP client so MCP retrieval receives the same authorization context as CLI and REST calls. Set `KIP_ROLES=admin` only for a verified reviewer that must list or review ontology-discovery candidates.

## 6. Connect real sources

Edit `config/kip.toml` and `.env`.

For an AI-operated real-corpus run, read [`docs/AI_OPERATOR_RUNBOOK.md`](docs/AI_OPERATOR_RUNBOOK.md) after the root agent files and contract documents.

### NAS

Mount NAS paths read-only. For Docker app mode, set `KIP_NAS_PATH` and Compose mounts it at `/sources/nas:ro`.

### HWP/HWPX

Reference parser order:

1. native `hwp-hwpx-parser` adapter with bounded evidence units;
2. disabled-by-default, preinstalled `kordoc`/`unhwp` subprocess broker;
3. paired PDF fallback;
4. manual review when all parsers fail.

Parser binaries are not hidden inside Core. Existing HWP/HWPX indexes can be
evaluated and promoted without a full source sync:

```bash
./scripts/kip parser reextract --source SOURCE_NAME
./scripts/kip parser reextract --source SOURCE_NAME --activate
```

The first command is non-mutating shadow work. The second retains the previous
extraction and swaps the active PostgreSQL/lexical state per document only
after revision, hash, ACL, classification, and quality checks. See
`docs/CONNECTORS.md`.

### PPTX

The pinned `extractors` extra installs `python-pptx`. Filesystem sources that
include `.pptx` emit shape-level evidence with exact slide/shape locators;
merged tables, chart caches, image hashes/alt text, groups, speaker notes,
legacy comments, and SmartArt text remain structured. Embedded OLE, media
transcription, modern threaded comments, and legacy `.ppt` are explicit
limitations rather than silent text loss. The reference profiles enable local
Korean OCR for scanned PDF candidates and PPTX pictures:

```bash
./scripts/install-kordoc.sh
./scripts/kordoc models --status
KORDOC_OFFLINE=1 ./scripts/kip sync run --source company-nas
```

Bootstrap and the production image verify every PP-OCRv5 Korean file before
indexing. KIP requires exact Kordoc 4.7.3, rejects runtime `npm`/`npx` commands,
bounds PPTX image batches, and keeps native evidence when OCR fails. Recognition
output is candidate evidence; review low-confidence warnings before making
material claims. Existing deployments keep their current `config/kip.toml`;
rerun bootstrap and opt in explicitly when upgrading an older configuration.

### Slack

Set `KIP_SLACK_BOT_TOKEN` and configure allowed conversation IDs. The connector uses Slack APIs as a source sync mechanism; local search does not depend on Slack search.

### Apple Mail

The macOS host adapter uses JXA through `osascript`. It requires explicit Mail Automation permission and account/mailbox allowlists. It does not read Mail's private database directly.

### IMAP

Use an app password or organization-approved credential. The connector uses UID cursors and stores RFC Message-ID as the stable message identity where available.

## 7. Ontology curation loop

Model, parser, and relation-miner outputs are candidates until a human approves
them; nothing is silently promoted to a fact. The agent-mediated loop is:

```bash
./scripts/kip ontology mine --unit-id UNIT_ID    # propose candidates from indexed evidence
./scripts/kip jobs list                          # mining job status, per-proposal skip reasons
./scripts/kip review list                        # triaged listing: risk desc, confidence desc,
                                                 # display names, Korean labels, evidence quotes
./scripts/kip review approve CANDIDATE_ID        # --supersede-contradicted to resolve conflicts
./scripts/kip review revoke ASSERTION_ID --note "reason"   # undo an approval
```

Relation candidates can only reference already-approved entities, so mining is
two-pass: mine, approve entity candidates, mine again, approve relations
(approving entities changes the mining digest, so the re-run is real). Approved
entities expand search aliases; approved assertions feed `kip answer`/`kip
context` with exact evidence and are dropped automatically when their source
changes. The same operations are exposed over REST and MCP
(`kip_jobs`, `kip_ontology_assertion_revoke`). See `docs/ONTOLOGY_GUIDE.md`.

## 8. Operations

- `GET /readyz` performs a real database round-trip (the production compose
  healthcheck targets it); `/healthz` stays liveness-only.
- `./scripts/ops-report.sh` checks failed jobs, queue age, last successful
  sync, disk free, backup age, and API health in one command (`--json`,
  `KIP_OPS_WEBHOOK` for failure notifications).
- `./scripts/backup.sh --retain N` produces sealed, checksum-verified backups
  with retention pruning; `./scripts/install-launchd.sh` schedules daily
  backups, periodic sync, optional ops reports, and generates a newsyslog
  rotation policy on macOS (`--dry-run` to preview).
- Files deleted from a filesystem source are tombstoned only after they stay
  absent for `[sync] deletion_grace_scans` consecutive complete scans
  (default 2); failed or empty scans never trigger deletion, and reappearing
  files re-index automatically.

Details are in `docs/OPERATIONS.md` and `docs/AI_OPERATOR_RUNBOOK.md`.

## 9. Deployment profiles

| Profile | Contents |
|---|---|
| Minimal | PostgreSQL, filesystem source, lexical search, CLI |
| Standard | Minimal + API, worker, HWP broker, Slack/Mail optional connectors |
| Expanded | Standard + explicitly activated semantic retrieval, relation miner, optional Neo4j projection; review remains headless CLI/API |

## 10. Important limitations of this starter

This is an implementation-ready starter, not a claim that every production
adapter is complete. The filesystem, text, PDF, XLSX shallow/deep path, memory
repository, CLI/API contracts, PostgreSQL migrations, and pgvector shadow path
are concrete; the local semantic path has been validated on the documented
Apple Silicon pilot but remains shadow-only for the private corpus. Slack,
Apple Mail, IMAP, and Neo4j remain environment-specific reference adapters;
the stdio MCP adapter is implemented and uses the shared application services.
The supported PostgreSQL reference profile includes pgvector and the 1024d HNSW
index even while semantic search is disabled; installation is not activation.
Existing unchanged HWP and HWPX revisions use the explicit
shadow/activate re-extraction workflow when parser versions change; no generic
all-format forced re-index command is exposed.

The starter lexical path locally reranks up to 40 ACL-filtered candidates with
candidate-local BM25; RapidFuzz 3.14.5 is the fallback. On the reviewed 19-case
private set, the final BM25 configuration reached Recall@10/MRR
`0.789/0.646` versus RapidFuzz `0.737/0.576`. This is retrieval evidence, not
reviewed answer or ontology quality evidence; reranking cannot recover a
document absent from the lexical candidate set.

Run `./scripts/verify.sh` before modifying or deploying the project.

Dependency PRs and parser/model upstream notifications are candidate-discovery
features. No update activates automatically; every one must pass shadow
evaluation and human promotion approval.

## 11. Reproducible RAG scorecard

KIP includes a licensed Korean public pilot, an isolated local model sidecar,
pgvector shadow spaces, lexical/vector/hybrid/reranked evaluation, and
timestamped JSON/Markdown reports.

```bash
make fetch-corpus
./scripts/fetch_public_corpus.py --check
./scripts/kip sync run --source public-government
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
```

Then, from another terminal:

```bash
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
make evaluate
```

The current public result keeps semantic search disabled: corrected lexical
retrieval reached Recall@10 and MRR 1.000 with zero ACL leaks, while the
semantic variants did not improve quality. The reviewed private result is
different: vector-only Recall@10/MRR reached `0.947/0.822` versus lexical
`0.789/0.646`, with HNSW P95 `133.75 ms` and zero ACL leaks. Stale-warning
coverage is still absent, so it remains disabled under the fail-closed gate.
Exact results, latency, fingerprints, target gaps,
and improvement history are in `docs/PRODUCTION_DESIGN_ALIGNMENT.md`,
`docs/RAG_EVALUATION.md` and `evaluation/reports/`.

The loaded-corpus parser, retrieval, semantic, graph, and ontology audit is in
[`docs/RAG_QUALITY_AUDIT_2026-08-06.md`](docs/RAG_QUALITY_AUDIT_2026-08-06.md).
