# KIP Knowledge Fabric Starter Kit v3.2

KIP is an agent-first, evidence-first foundation for indexing and retrieving company knowledge from NAS files, HWP/HWPX, PDF, XLSX, Slack, and email. It supports two equal entry paths:

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
- CLI, REST, and MCP use the same application layer.
- Search, graph, and embeddings are replaceable projections.

## 2. Agent-guided setup

새 배포에서는 설정 파일을 직접 추측해 편집하지 말고 AI agent에게 다음처럼
명령한다.

```text
KIP을 셋업해줘
```

Agent는 `skills/kip-setup/SKILL.md`를 따라 `kip setup inspect`가 반환한 질문을
항상 하나씩 묻는다. 조직 workspace, 신원 검증, 수집할 각 폴더의 절대경로와
확장자·제외 범위·등급·ACL, 모델 전송 정책, secret reference, CAS/backup,
평가 dataset, 빈 starter 또는 example ontology profile, interaction-memory
explicit consent, 온톨로지 reviewer를 모두 확정한다. 이어서 파일 수·용량·확장자
분포·제외·symlink를 preview하고, 사용자가 plan fingerprint와 read-only mount를
승인한 뒤에만 다음 파일을 원자적으로 생성·검증한다.

- `config/kip.generated.toml`
- `compose.generated.yaml`
- `.mcp.json` pointing at the generated runtime configuration

Credential 원문은 질문, state, plan, 생성 파일에 넣지 않는다. `env:`,
`keychain:`, `secret-manager:` reference만 기록한다. 평가 dataset이 없으면
설치 가능한 상태일 뿐 production 승격이 아님을 receipt에 남긴다. 전체 인수
절차는 [`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md)에 있다.

## 3. Quick start - local development

다른 조직이나 저장소에 적용할 때는 먼저
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md)를 따른다. 이 문서는
환경별 결정, AI 변경 계약, 실제 자료 인수 테스트, 자동 업데이트 알림과
승격/rollback 기준을 한 경로로 묶는다.

Prerequisites: Python 3.12+ and Docker with Compose. Node.js is needed only if
an operator explicitly installs and enables a Kordoc command fallback; normal
indexing never downloads parser packages at runtime.

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

인터넷 경계에서는 임의 workspace, principal, ACL header를 신뢰하지 않는다.
API-key bootstrap은 설정에 고정된 단일 principal을 사용하고, 다중 사용자는
검증된 JWT claim에서 workspace와 scope를 파생한다.

CLI, REST, MCP, and connector events all enter the same application service layer. Applications must not connect directly to PostgreSQL or an optional graph projection.

## 5. Claude Code and MCP

Claude Code loads root `CLAUDE.md`, which imports `AGENTS.md`. The project skill lives at:

```text
.claude/skills/knowledge-fabric/SKILL.md
```

The root `.mcp.json` starts the optional stdio MCP adapter without embedding secrets in the file. Guided setup rewrites it atomically to select `config/kip.generated.toml` and preserves the previous file. `./scripts/bootstrap.sh` installs the MCP and identity runtimes. For a standalone package install, select the MCP extra explicitly:

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
./scripts/kip parser reextract --source company-nas
./scripts/kip parser reextract --source company-nas --activate
```

The first command is non-mutating shadow work. The second retains the previous
extraction and swaps the active PostgreSQL/lexical state per document only
after revision, hash, ACL, classification, and quality checks. See
`docs/CONNECTORS.md`.

### Slack

Set `KIP_SLACK_BOT_TOKEN` and configure allowed conversation IDs. The connector uses Slack APIs as a source sync mechanism; local search does not depend on Slack search.

### Apple Mail

The macOS host adapter uses JXA through `osascript`. It requires explicit Mail Automation permission and account/mailbox allowlists. It does not read Mail's private database directly.

### IMAP

Use an app password or organization-approved credential. The connector uses UID cursors and stores RFC Message-ID as the stable message identity where available.

## 7. Deployment profiles

| Profile | Contents |
|---|---|
| Minimal | PostgreSQL, filesystem source, lexical search, CLI |
| Standard | Minimal + API, worker, HWP broker, Slack/Mail optional connectors |
| Expanded | Standard + explicitly activated semantic retrieval, relation miner, optional Neo4j projection; review remains headless CLI/API |

## 8. Important limitations of this starter

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

Dependency PR과 parser/model upstream 알림은 후보 발견 기능이다. 어떤
업데이트도 자동 활성화하지 않으며, shadow 평가와 사람의 승격 승인을
거쳐야 한다.

## 9. Reproducible RAG scorecard

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
