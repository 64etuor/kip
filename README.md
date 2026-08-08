# KIP Knowledge Fabric Starter Kit v3.1

KIP is an agent-first, evidence-first foundation for indexing and retrieving company knowledge from NAS files, HWP/HWPX, PDF, XLSX, Slack, and email. It supports two equal entry paths:

- **Agent/terminal path:** stable CLI commands that emit versioned JSON.
- **Application path:** REST/OpenAPI and optional MCP adapters backed by the same services.

The baseline runtime is PostgreSQL 18 with PostgreSQL-native lexical search. `pgvector` is installed by the reference image but semantic search remains disabled until an evaluation proves value. Neo4j is not canonical and is only a future read projection.

## 1. Repository guarantees

- `AGENTS.md` and `CLAUDE.md` are at the project root.
- `CLAUDE.md` imports `AGENTS.md` with `@AGENTS.md`.
- NAS and connector sources are read-only.
- HWP/HWPX uses the measured native `hwp-hwpx-parser` adapter first, with replaceable command and paired-PDF fallbacks.
- XLSX uses shallow-all / deep-candidate retrieval.
- CLI, REST, and MCP use the same application layer.
- Search, graph, and embeddings are replaceable projections.

## 2. Quick start - local development

다른 조직이나 저장소에 적용할 때는 먼저
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md)를 따른다. 이 문서는
환경별 결정, AI 변경 계약, 실제 자료 인수 테스트, 자동 업데이트 알림과
승격/rollback 기준을 한 경로로 묶는다.

Prerequisites: Python 3.12+, Docker with Compose, and optionally Node.js 18+ for `kordoc`.

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

## 3. Run as an application

Start the API locally:

```bash
./scripts/api.sh
```

Then call it from another app:

```bash
curl -sS http://127.0.0.1:8080/v1/search \
  -H "Content-Type: application/json" \
  -H "X-KIP-Workspace: default" \
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
  -H "X-KIP-Workspace: default" \
  -H "X-KIP-ACL-Scopes: workspace:default,project:A" \
  --data-binary @examples/connector/event.json
```

CLI, REST, MCP, and connector events all enter the same application service layer. Applications must not connect directly to PostgreSQL or an optional graph projection.

## 4. Claude Code and MCP

Claude Code loads root `CLAUDE.md`, which imports `AGENTS.md`. The project skill lives at:

```text
.claude/skills/knowledge-fabric/SKILL.md
```

The root `.mcp.json` starts the optional stdio MCP adapter without embedding secrets in the file. Install the MCP extra first:

```bash
python -m pip install -e '.[mcp]'
```

MCP is optional; the CLI remains the lowest-dependency agent interface.
Set `KIP_WORKSPACE`, `KIP_PRINCIPAL_ID`, and `KIP_ACL_SCOPES` in the environment used by the MCP client so MCP retrieval receives the same authorization context as CLI and REST calls.

## 5. Connect real sources

Edit `config/kip.toml` and `.env`.

For an AI-operated real-corpus run, read [`docs/AI_OPERATOR_RUNBOOK.md`](docs/AI_OPERATOR_RUNBOOK.md) after the root agent files and contract documents.

### NAS

Mount NAS paths read-only. For Docker app mode, set `KIP_NAS_PATH` and Compose mounts it at `/sources/nas:ro`.

### HWP/HWPX

Reference parser order:

1. native `hwp-hwpx-parser` adapter with bounded evidence units;
2. configurable `kordoc`/`unhwp` subprocess broker;
3. paired PDF fallback;
4. manual review when all parsers fail.

Parser binaries are not hidden inside Core. See `docs/CONNECTORS.md`.

### Slack

Set `KIP_SLACK_BOT_TOKEN` and configure allowed conversation IDs. The connector uses Slack APIs as a source sync mechanism; local search does not depend on Slack search.

### Apple Mail

The macOS host adapter uses JXA through `osascript`. It requires explicit Mail Automation permission and account/mailbox allowlists. It does not read Mail's private database directly.

### IMAP

Use an app password or organization-approved credential. The connector uses UID cursors and stores RFC Message-ID as the stable message identity where available.

## 6. Deployment profiles

| Profile | Contents |
|---|---|
| Minimal | PostgreSQL, filesystem source, lexical search, CLI |
| Standard | Minimal + API, worker, HWP broker, Slack/Mail optional connectors |
| Expanded | Standard + pgvector activation, relation miner, optional Neo4j projection, review UI |

## 7. Important limitations of this starter

This is an implementation-ready starter, not a claim that every production
adapter is complete. The filesystem, text, PDF, XLSX shallow/deep path, memory
repository, CLI/API contracts, PostgreSQL migrations, and pgvector shadow path
are concrete; the local semantic path has been validated on the documented
Apple Silicon pilot but remains shadow-only for the private corpus. Slack,
Apple Mail, IMAP, MCP, and Neo4j remain reference adapters that require
environment-specific validation before production use. Existing unchanged
revisions require an explicit re-extraction workflow when parser versions
change.

Run `./scripts/verify.sh` before modifying or deploying the project.

Dependency PR과 parser/model upstream 알림은 후보 발견 기능이다. 어떤
업데이트도 자동 활성화하지 않으며, shadow 평가와 사람의 승격 승인을
거쳐야 한다.

## 8. Reproducible RAG scorecard

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
semantic variants did not improve quality. The implementation remains a
complete shadow path for harder, explicitly allowlisted corpora. Exact results,
latency, fingerprints, and improvement history are in
`docs/RAG_EVALUATION.md` and `evaluation/reports/`.

The loaded-corpus parser, retrieval, semantic, graph, and ontology audit is in
[`docs/RAG_QUALITY_AUDIT_2026-08-06.md`](docs/RAG_QUALITY_AUDIT_2026-08-06.md).
