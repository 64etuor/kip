# KIP 지식 패브릭 스타터킷 v3.6

KIP은 NAS 파일, HWP/HWPX, PDF, PPTX, XLSX, Slack, 이메일에 흩어진 회사
지식을 색인하고 근거와 함께 검색하기 위한 에이전트 우선·증거 우선 기반입니다.
두 진입 경로는 같은 애플리케이션 서비스를 사용합니다.

- **에이전트/터미널 경로:** 버전이 지정된 JSON을 출력하는 안정적인 CLI 명령
- **애플리케이션 경로:** 동일한 서비스를 사용하는 REST/OpenAPI 및 선택형 MCP 어댑터

기준 런타임은 PostgreSQL 18과 PostgreSQL 기본 lexical search입니다.
`pgvector`는 기준 이미지에 설치되지만, 평가로 효용을 입증하기 전까지 semantic
search는 비활성 상태입니다. Neo4j는 canonical store가 아니며 향후 선택형 read
projection으로만 취급합니다.

승인된 목표, 현재 구현, 측정 근거, 남은 운영 격차는
[`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md)에
분리해 기록합니다. PRD/TRD의 목표 문구만 보고 현재 준비 상태를 추정하지 마세요.

자연어 요청에서 실제 MCP 도구 선택과 근거 읽기를 점검한 결과는
[`에이전트 품질 기록`](docs/AGENT_QUALITY.md)에 있습니다. 합성 사례 7건의
결과이며 모든 문서·모델의 답변 품질을 보장하는 수치는 아닙니다.

## 검증된 소스 ZIP

인터넷 연결 환경에 소스와 필수 운영 문서만 전달하려면 작업 디렉터리를 직접
압축하지 말고 결정적 스타터 ZIP을 만듭니다.

```bash
./scripts/build-starter-kit.sh
./scripts/verify-starter-kit.sh dist/kip-starter-kit-$(cat VERSION).zip
```

ZIP은 하나의 버전 디렉터리 아래에 구현 코드, 잠긴 의존성, 테스트, migration,
contract, ontology, 예제, 자동화, canonical 운영 문서를 담습니다. 로컬 설정,
credential, DB, CAS/output, private 평가 자료, 내부 plan, 생성형 package metadata,
릴리스 바이너리는 제외합니다. `STARTER-KIT-MANIFEST.json`, 내부 `SHA256SUMS`,
외부 `.zip.sha256` 파일로 전달물을 독립적으로 검증할 수 있습니다. 정식 릴리스는
clean tree에서 만들고, `--allow-dirty`는 이름이 명확한 로컬 candidate에만 씁니다.

## 0. 처음 오셨나요?

전문 용어 없이 요약하면 KIP은 **회사 문서를 모아 검색하고, 답변의 근거가 된
원문 위치를 항상 함께 제시하는 시스템**입니다.

- 용어가 어렵다면 → [`docs/GLOSSARY.md`](docs/GLOSSARY.md)
- 설치부터 하고 싶다면 → [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- 설치 후 매일 쓰는 법 → [`docs/OPERATIONS.md`](docs/OPERATIONS.md)

가장 쉬운 설치 방법은 AI 에이전트에게 `KIP을 셋업해줘`라고 요청하는 것입니다.
에이전트가 필요한 질문을 하나씩 묻고 설정 파일을 생성합니다.

세 인터페이스를 모두 설치할 필요는 없습니다. 터미널에서 직접 사용하면 CLI로
충분하고, 사내 프로그램과 연동하려면 REST API를, Claude 같은 AI 비서와
연결하려면 MCP를 사용합니다. 세 경로의 내부 동작과 권한 규칙은 같습니다.

## 1. 저장소가 보장하는 것

- 프로젝트 루트에 `AGENTS.md`와 `CLAUDE.md`가 있습니다.
- `CLAUDE.md`는 `@AGENTS.md`로 지침을 불러옵니다.
- NAS와 connector 원본은 읽기 전용입니다.
- 기준 filesystem parser는 문서마다 제한된 별도 child process에서 실행됩니다.
  원본 권한과 network denial은 배포 계층에서 별도로 강제합니다.
- HWP/HWPX는 측정된 `hwp-hwpx-parser`를 우선 사용하고, 교체 가능한 command와
  paired-PDF fallback을 제공합니다.
- PDF는 `pdf-inspector`를 기본으로 쓰며 선택적 PyMuPDF 표 fallback을 제공합니다.
- XLSX는 shallow-all / deep-candidate 검색 전략을 사용합니다.
- PPTX는 macro 실행이나 외부 relationship fetch 없이 slide/shape 구조, 표,
  chart cache, image metadata, note, comment, SmartArt text, geometry를 보존합니다.
- CLI, REST, MCP는 동일한 application layer를 사용합니다.
- Search, graph, embedding은 다시 만들 수 있는 교체형 projection입니다.

## 2. AI 에이전트 기반 설정

새 배포에서 추측으로 설정 파일을 직접 편집하지 마세요. AI 에이전트에게 다음 한
문장을 전달합니다.

```text
KIP을 셋업해줘
```

에이전트는 `skills/kip-setup/SKILL.md`에 따라 `kip setup inspect`가 반환하는
질문을 한 번에 하나씩 묻습니다. 조직 workspace, 신원 검증, 색인할 각 폴더의
절대 경로와 확장자/제외 범위, classification, ACL, model egress, 검토형 ontology
relation mining 사용 여부, secret reference, CAS/backup 경로, 평가 dataset,
ontology profile, interaction-memory 동의, reviewer를 확정합니다. 이후 file count,
크기, 확장자 분포, 제외 항목, symlink를 미리 보여주고 사용자가 plan fingerprint와
read-only mount를 승인한 뒤 다음 파일을 원자적으로 생성·검증합니다.

- `config/kip.generated.toml`: container 설정
- `config/kip.host.generated.toml`: host/MCP 설정 (source 절대경로는 container와 동일)
- `compose.generated.yaml`
- 생성된 host config를 가리키는 `.mcp.json`

질문, state, plan, 생성 파일에는 raw credential을 기록하지 않습니다. 런타임이
해석할 수 있는 `env:` reference만 저장하고, model credential에 한해 `file:`도
허용합니다. `keychain:`과 `secret-manager:`는 거부합니다. 평가 dataset이 없으면
설치는 가능하지만 production-promoted 상태가 아니라는 제한을 receipt에 남깁니다.

`setup apply`는 설정을 생성하고 `setup verify`는 파일과 runtime 준비 항목을
검증합니다. 실제 배포는 `./scripts/app-up.sh`부터 실행한 뒤 source sync,
search와 exact-read smoke까지 확인합니다. 승인한 Compose만 사용하며 DB 준비와
migration 순서는 자동 처리합니다. 폴더 경로만 답해도 보수적 분류와 workspace
ACL을 제안하고, preview가 local/cloud-only 파일을 구분합니다. 다운로드가 필요한
파일은 OneDrive 앱에서 먼저 선택합니다. 전체 인수 절차는
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md)를 따릅니다.

검색 범위는 활성 source에 지정한 디렉터리 하위로 제한됩니다. 제거·비활성화·
범위 변경 후 서비스가 설정을 다시 읽으면 이전 색인과 알려진 ID에도 새 경계가
적용됩니다. 변경 범위를 다시 허용하려면 명시적인 sync가 필요합니다.

## 3. 로컬 개발 빠른 시작

다른 조직이나 저장소에서 이 킷을 도입한다면 먼저
[`docs/STARTER_KIT_GUIDE.md`](docs/STARTER_KIT_GUIDE.md)를 읽으세요. 환경별
결정, AI 변경 계약, 실제 corpus 인수 테스트, update notification, 승격/rollback
기준이 한 경로에 정리돼 있습니다.

### 준비물

Python 3.12+, Node.js 20.9+, Docker Compose, 최소 10GB의 여유 디스크가
필요합니다. 런타임 이미지 약 2GB, OCR 모델 약 0.8GB, Python 환경 약 1GB,
데이터베이스 공간을 포함한 최소치입니다.

```bash
python3 --version        # 3.12 이상
node --version           # 20.9 이상
docker compose version   # Docker Desktop 실행 상태
df -h .                  # 여유 공간 10GB 이상
```

스크립트는 bash를 사용합니다. macOS와 Linux에서는 바로 실행할 수 있고,
Windows에서는 PowerShell/cmd가 아니라 WSL2 Ubuntu 안에서 실행해야 합니다.
한 줄이라도 실패하면 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)를
확인하세요.

Bootstrap은 `uv.lock`의 고정된 의존성을 `uv sync --frozen`으로 설치하고,
고정된 Kordoc OCR runtime과 한국어 model cache를 설치·검증합니다.
OCR npm 의존성도 lock 파일로 고정하며 설치 후처리를 실행하지 않습니다.
알려진 high 이상 취약점이나 감사 실패가 있으면 설치를 중단합니다.
uv가 없으면 별도 도구 환경에 0.8.22를 준비합니다. 새 `.env`에는 무작위
credential을 생성하며 기존 `.env`와 config는 보존합니다.
정상 색인 중에는 parser package나 model을 내려받지 않습니다.

```bash
./scripts/bootstrap.sh
./scripts/dev-up.sh
./scripts/migrate.sh
./scripts/doctor.sh
./scripts/test.sh
```

포함된 sample data를 색인합니다.

```bash
./scripts/kip sync run --source sample

./scripts/kip search "참여율 변경 승인" --limit 10
./scripts/kip context "정산 증빙 제출기한" --limit 5
```

### 정상 동작 확인

성공하면 `search` 결과 JSON에 `"ok": true`가 있고 `data.results` 배열에 항목이
하나 이상 있습니다. 결과가 비어 있다면 다음 순서로 확인합니다.

```bash
./scripts/kip status   # data.content_units가 0보다 커야 함
./scripts/kip doctor   # ok:false 항목의 reason 확인
```

`content_units`가 0이면 `./scripts/kip sync run --source sample`을 먼저 실행했는지
확인하세요. 계속 막히면 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)를
참조하세요.

## 4. 애플리케이션으로 실행

로컬 API를 시작합니다.

```bash
./scripts/api.sh
```

다른 애플리케이션에서 호출합니다.

```bash
curl -sS http://127.0.0.1:8080/v1/search \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -d '{"query":"협약 변경 승인","limit":5}'
```

Python client는 `sdk/python/kip_client.py`에 있습니다. OpenAPI는 FastAPI app에서
생성되며 release 검증 시 `contracts/openapi.json`에 기록됩니다.

외부 애플리케이션은 connector endpoint로 정규화된 source 변경을 전달할 수도
있습니다.

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  --data-binary @examples/connector/event.json
```

인터넷 경계에서는 임의의 workspace, principal, ACL header를 신뢰하지 않습니다.
API-key bootstrap은 config에 고정된 단일 principal을 사용하고, 다중 사용자 배포는
검증된 JWT claim에서 workspace와 scope를 가져옵니다. 애플리케이션은 PostgreSQL이나
선택형 graph projection에 직접 연결하지 말고 CLI/REST/MCP와 동일한 application
service layer를 사용해야 합니다.

## 5. Claude Code와 MCP

Claude Code는 루트 `CLAUDE.md`를 읽고, 이 파일은 `AGENTS.md`를 import합니다.
프로젝트 skill은 다음 경로에 있습니다.

```text
.claude/skills/knowledge-fabric/SKILL.md
```

루트 `.mcp.json`은 secret을 넣지 않고 선택형 stdio MCP adapter를 시작합니다.
AI 기반 setup은 이 파일을 원자적으로 갱신해 host 경로용
`config/kip.host.generated.toml`을 선택하고 이전 파일을 보존합니다. Container 경로용
`config/kip.generated.toml`과 혼동하지 마세요. 저장소 checkout에서는 MCP extra를
명시적으로 선택할 수 있습니다.

```bash
uv sync --extra mcp
```

Adapter는 안정화된 MCP 2.x SDK를 사용하고 KIP package version을 보고하며, 현재
protocol과 SDK가 지원하는 legacy client를 협상합니다. Tool 결과는 계속
`kip.envelope.v1`이고 MCP metadata는 권한 계약이 아닙니다. 제공 adapter는 stdio
전용이며 sampling, elicitation, roots, protocol logging을 사용하지 않습니다.

MCP client 실행 환경에 `KIP_WORKSPACE`, `KIP_PRINCIPAL_ID`, `KIP_ACL_SCOPES`를
설정해 CLI/REST와 동일한 authorization context를 사용하세요. 검증된 reviewer가
ontology discovery를 검토할 때만 `KIP_ROLES=admin`을 설정합니다.

## 6. 실제 source 연결

`config/kip.toml`과 `.env`를 편집합니다. AI가 실제 corpus를 운영한다면 루트 agent
지침과 contract 문서 다음으로
[`docs/AI_OPERATOR_RUNBOOK.md`](docs/AI_OPERATOR_RUNBOOK.md)를 읽어야 합니다.

### NAS

NAS 경로는 읽기 전용으로 mount합니다. Docker app mode에서는 `KIP_NAS_PATH`를
설정하며 Compose가 이를 `/sources/nas:ro`에 mount합니다.

### HWP/HWPX

기준 parser 순서는 다음과 같습니다.

1. 제한된 evidence unit을 만드는 native `hwp-hwpx-parser`
2. 기본 비활성 상태의 사전 설치 `kordoc`/`unhwp` subprocess broker
3. paired PDF fallback
4. 모든 parser 실패 시 수동 검토

Parser binary는 Core에 숨겨 넣지 않습니다. 기존 HWP/HWPX index는 전체 source sync
없이 평가하고 승격할 수 있습니다.

```bash
./scripts/kip parser reextract --source SOURCE_NAME
./scripts/kip parser reextract --source SOURCE_NAME --activate
```

첫 명령은 non-mutating shadow 작업입니다. 두 번째 명령은 revision, hash, ACL,
classification, quality를 확인한 뒤 문서별로 이전 extraction을 보존하면서 active
PostgreSQL/lexical state를 교체합니다. 자세한 내용은 `docs/CONNECTORS.md`를
참조하세요.

### PDF

새 starter profile은 로컬 `pdf-inspector` 1.14.2로 구조화된 page Markdown,
layout/table signal, page별 OCR routing을 만듭니다. 유효한 Markdown table은
`pdf_table` evidence가 되고, 표가 감지됐지만 구조화 Markdown이 없으면 그
page에만 PyMuPDF `lines_strict` fallback을 적용합니다. 기존 `pymupdf` backend는
rollback 경로로 유지합니다.

```toml
[parsers.pdf]
backend = "pdf_inspector" # 또는 "pymupdf"
tables_enabled = true     # pymupdf rollback backend에서 사용
```

기존 PDF index는 operator가 shadow re-extraction을 실행하고 candidate를 명시적으로
활성화하기 전까지 바뀌지 않습니다. 원본 파일은 항상 읽기 전용입니다.

### PPTX

고정된 `extractors` extra가 `python-pptx`를 설치합니다. `.pptx`를 포함한 source는
정확한 slide/shape locator를 가진 shape-level evidence를 만들며 merged table,
chart cache, image hash/alt text, group, speaker note, legacy comment, SmartArt text를
구조화해 보존합니다. Embedded OLE, media transcription, modern threaded comment,
legacy `.ppt`는 조용히 유실하지 않고 명시적 제한으로 기록합니다.

기준 profile은 scanned PDF candidate와 PPTX image에 로컬 한국어 OCR을 사용합니다.

```bash
./scripts/install-kordoc.sh
./scripts/kordoc models --status
KORDOC_OFFLINE=1 ./scripts/kip sync run --source company-nas
```

Bootstrap과 production image는 색인 전에 모든 PP-OCRv5 Korean 파일을 검증합니다.
KIP은 Kordoc 4.8.0만 허용하고 runtime `npm`/`npx` 명령을 거부하며 PPTX image
batch를 제한합니다. OCR 실패 시에도 native evidence는 유지됩니다. 인식 결과는
candidate evidence이므로 중요한 주장 전에 low-confidence warning을 검토하세요.
기존 배포는 현재 `config/kip.toml`을 유지하므로 bootstrap을 다시 실행한 뒤
의도적으로 opt-in해야 합니다.

### Slack

`KIP_SLACK_BOT_TOKEN`과 허용 conversation ID를 설정합니다. Connector는 Slack
API를 source sync에만 사용하며 로컬 search는 Slack search에 의존하지 않습니다.

### Apple Mail

macOS host adapter는 `osascript`를 통한 JXA를 사용합니다. 명시적인 Mail
Automation 권한과 account/mailbox allowlist가 필요하며 Mail의 private DB를 직접
읽지 않습니다.

### IMAP

App password 또는 조직이 승인한 credential을 사용합니다. Connector는 UID
cursor를 사용하고 가능한 경우 RFC Message-ID를 안정적인 message identity로
저장합니다.

## 7. Ontology 검토 루프

Model, parser, relation-miner 출력은 사람이 승인하기 전까지 candidate입니다.
어떤 결과도 사실로 조용히 승격하지 않습니다.

```bash
./scripts/kip ontology mine --unit-id UNIT_ID
./scripts/kip jobs list
./scripts/kip review list
./scripts/kip review approve CANDIDATE_ID
./scripts/kip review revoke ASSERTION_ID --note "reason"
```

Relation candidate는 승인된 entity만 참조하므로 mine → entity 승인 → 다시 mine →
relation 승인 순서로 진행합니다. Entity 승인은 mining digest를 바꾸므로 두 번째
실행은 실제 재평가입니다. 승인된 entity는 search alias를 확장하고, 승인 assertion은
exact evidence와 함께 `kip answer`/`kip context`에 들어갑니다. Source가 바뀌면
자동으로 제외됩니다. 동일 기능은 REST와 MCP에도 제공됩니다. 자세한 내용은
`docs/ONTOLOGY_GUIDE.md`를 참조하세요.

## 8. 운영

- `GET /readyz`는 실제 DB round-trip을 수행하고 production compose healthcheck가
  이를 사용합니다. `/healthz`는 liveness 전용입니다.
- `./scripts/ops-report.sh`는 failed job, queue age, 마지막 성공 sync, disk free,
  backup age, API health를 확인합니다. `--json`과 실패 알림용
  `KIP_OPS_WEBHOOK`을 지원합니다.
- `./scripts/backup.sh --retain N`은 seal과 checksum이 있는 backup을 만들고
  retention을 적용합니다. `./scripts/install-launchd.sh`는 macOS에서 daily backup,
  periodic sync, 선택형 ops report, newsyslog rotation policy를 설정합니다.
- Filesystem source에서 삭제된 파일은 `[sync] deletion_grace_scans`번의 연속된
  complete scan에서 계속 없을 때만 tombstone됩니다. 기본값은 2입니다. Failed/empty
  scan은 삭제 근거가 아니며 다시 나타난 파일은 자동 재색인됩니다. Directory walk
  오류는 scan을 실패시키고 settle/filter/size/symlink-policy 보류 파일은 present로
  유지합니다.

세부 절차는 `docs/OPERATIONS.md`와 `docs/AI_OPERATOR_RUNBOOK.md`에 있습니다.

## 9. 배포 profile

| Profile | 구성 |
|---|---|
| 최소 | PostgreSQL, filesystem source, lexical search, CLI |
| 표준 | 최소 profile + API, worker, HWP broker, 선택형 Slack/Mail connector |
| 확장 | 표준 + opt-in semantic/관계 추출/Neo4j. 검토는 CLI/API로 제공 |

## 10. 현재 제한 사항

이 저장소는 구현 가능한 starter이지 모든 production adapter가 완성됐다는 주장이
아닙니다. Filesystem, text, PDF, XLSX shallow/deep, memory repository, CLI/API
contract, PostgreSQL migration, pgvector shadow path는 구현돼 있습니다. 로컬 semantic
path는 문서화된 Apple Silicon pilot에서 검증됐지만 private corpus에서는 계속
shadow-only입니다. Slack, Apple Mail, IMAP, Neo4j는 환경별 reference adapter입니다.
stdio MCP adapter는 동일 application service를 사용하도록 구현돼 있습니다.

지원하는 PostgreSQL profile은 semantic search가 꺼져 있어도 pgvector와 1024d HNSW
index를 포함합니다. 설치는 활성화가 아닙니다. 변경되지 않은 HWP/HWPX revision은
parser version이 바뀔 때 명시적 shadow/activate re-extraction을 사용합니다. 모든
format을 강제로 다시 색인하는 일반 명령은 제공하지 않습니다.

Starter lexical path는 ACL-filtered candidate 최대 40개를 candidate-local BM25로
rerank하며 RapidFuzz 3.14.5를 fallback으로 사용합니다. 검토된 private 19-case에서
최종 BM25는 Recall@10/MRR `0.789/0.646`, RapidFuzz는 `0.737/0.576`이었습니다.
이는 retrieval 근거이지 answer 또는 ontology 품질 근거가 아닙니다. Lexical candidate
set에 없는 문서는 reranking으로 복구할 수 없습니다.

수정이나 배포 전에 `./scripts/verify.sh`를 실행하세요. 필수 검사 도구가 없으면
실패하므로 `./scripts/bootstrap.sh`로 환경을 복구한 뒤 다시 실행합니다.
Dependency PR과 parser/model upstream 알림은 candidate discovery일 뿐 자동
활성화가 아닙니다. Parser/model 교체는 shadow 평가와 승격 결정을 거칩니다.
AI 지침은 `AGENTS.md`에서 작업별 문서를 찾아 필요한 부분만 읽도록 구성했습니다.

## 11. 재현 가능한 RAG scorecard

KIP에는 라이선스가 명확한 한국어 공개 pilot, 격리된 로컬 model sidecar,
pgvector shadow space, lexical/vector/hybrid/reranked 평가, timestamped JSON/Markdown
report가 포함돼 있습니다.

```bash
make fetch-corpus
./scripts/fetch_public_corpus.py --check
./scripts/kip sync run --source public-government
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
```

다른 터미널에서 실행합니다.

```bash
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
make evaluate
```

현재 공개 결과에서는 semantic search를 비활성 상태로 유지합니다. 수정된 lexical
retrieval은 ACL 누출 없이 Recall@10과 MRR 1.000을 기록했으며 semantic variant는
품질을 개선하지 못했습니다. 역사적으로 검토된 private `c4000` 결과는 다릅니다.
Vector-only Recall@10/MRR은 `0.947/0.822`, lexical은 `0.789/0.646`, HNSW P95는
`133.75 ms`, ACL 누출은 0이었습니다. 현재 12,000-character `c12000` identity는
아직 rebuild/evaluate되지 않았고 stale-warning coverage도 없으므로 fail-closed
gate에 따라 semantic search는 비활성 상태입니다.

정확한 결과, latency, fingerprint, target gap, 개선 이력은
`docs/PRODUCTION_DESIGN_ALIGNMENT.md`, `docs/RAG_EVALUATION.md`,
`evaluation/reports/`에 있습니다. 전체 corpus의 parser, retrieval, semantic,
graph, ontology audit는
[`2026-08-06 기록`](https://github.com/64etuor/kip/blob/1b04bad685762fe3002d9c4ec6a75f267df9fb94/docs/RAG_QUALITY_AUDIT_2026-08-06.md)에
기록돼 있습니다.

## 12. 라이선스

KIP 자체는 MIT License입니다. 전문은 [`LICENSE`](LICENSE)에 있습니다.

KIP는 서드파티 구성요소를 사용하며, **그중 두 개는 copyleft라 배포 방식에
따라 의무가 발생합니다.** 전체 목록과 각 구성요소의 라이선스는
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)에 있고, 특정 릴리스의
정확한 내역은 `make release`가 생성하는 SPDX SBOM이 기준입니다.

| 구성요소 | 라이선스 | 왜 중요한가 |
|---|---|---|
| PyMuPDF | **AGPL-3.0** 또는 Artifex 상용 | `requirements/runtime.txt`에 pin되어 **프로덕션 이미지에 포함**되고, 기본 PDF 경로의 표 fallback이 호출합니다 (ADR-054). AGPL은 네트워크 copyleft라 서비스로 제공할 때 해당 구성요소에 대한 소스 제공 의무가 따릅니다 |
| psycopg, psycopg-pool | **LGPL-3.0-only** | 공개 API를 그대로 사용하므로 KIP에 전이되지 않지만, 재배포 시 LGPL 고지 의무가 있습니다 |

나머지 의존성은 MIT, BSD, Apache-2.0, PostgreSQL License입니다.

PyMuPDF 없이 배포하려면 `[parsers.pdf] tables_enabled = false`로 두고
`extractors` extra를 제외한 뒤 runtime lock을 다시 생성하세요. PDF 기본 백엔드
`pdf-inspector`는 MIT이므로 표 fallback만 사라집니다. 실제 의무 판단은
법률 자문을 받으시기 바랍니다. 이 문서는 사실 목록이지 법률 자문이 아닙니다.
