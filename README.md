# KIP 지식 패브릭

**KIP은 NAS·HWP/HWPX·PDF·PPTX·XLSX·Slack·메일에 흩어진 사내 문서를 색인하고,
답변마다 원문 위치(locator)를 함께 돌려주는 에이전트 우선 지식 검색 기반입니다.**

## 설치

저장소를 복제하지 않고 최신 릴리스를 설치합니다. 설치 위치는 `$KIP_HOME`
또는 `~/kip`이며, 비어 있거나 아직 없는 디렉터리여야 합니다.

```bash
curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash
cd ~/kip && ./scripts/app-up.sh --database-only   # 번들 PostgreSQL 준비
```

## 첫 질의

그다음은 두 명령입니다. 패키지에 포함된 `sample-data/`를 색인하고 검색합니다.

```bash
kip sync run --source sample
kip search "참여율 변경 승인" --limit 1
```

아래는 그 두 명령을 실제로 실행해 받은 응답입니다. 꾸며낸 예시가 아니라
번들 sample data에 대한 실제 실행 결과이며, id와 hash만 줄여 적었습니다.

```json
{
  "schema_version": "kip.envelope.v1",
  "ok": true,
  "data": [
    {
      "unit_id": "unit_5551af4ab2b0…",
      "document_id": "ldoc_98f04ef1e649…",
      "artifact_id": "art_dddf941b4a5a…",
      "source_kind": "filesystem",
      "title": "A과제_메모.md",
      "snippet": "2026년 6월 15일 참여연구원 구성 변경으로 참여율 조정이 필요하다는 논의가 있었다. 공식 효력은 승인 공문과 협약변경 문서를 확인해야 한다.",
      "score": 3.662907300901048,
      "locator": {
        "type": "text_line_range",
        "data": { "end_line": 5, "start_line": 1 }
      },
      "source_uri": "file:///.../sample-data/A%E1%84%80%E1%85%AA%E1%84%8C%E1%85%A6_%E1%84%86%E1%85%A6%E1%84%86%E1%85%A9.md",
      "source_sha256": "79770d17e005…",
      "source_modified_at": "2026-09-12T15:25:10.140102Z",
      "metadata": {
        "file_name": "A과제_메모.md",
        "document_type": null,
        "is_latest": true,
        "retrieval_channels": ["lexical"],
        "lexical_rank": 1,
        "rrf_score": 5.0,
        "rerank_score": 3.662907300901048,
        "rerank_rank": 1,
        "rerank_model": "okapi-char-bigram-v1"
      },
      "evidence_role": "discovery",
      "source_verification": "not_checked"
    }
  ],
  "error": null,
  "meta": {
    "request_id": "req_ebc61f0c81a9…",
    "workspace": "default",
    "generated_at": "2026-09-12T15:25:21.592365Z",
    "warnings": []
  }
}
```

`snippet`은 발견용 단서일 뿐이므로, 인용하기 전에 `kip read unit_...`로 원문을
다시 열고 함께 오는 `source_verification`으로 신선도를 판단합니다.
`sha256`이나 `stat`일 때만 `source_changed_since_index`가 결론이며,
`unavailable`이면 그 값은 `null`이고 확인 불가를 뜻합니다. 최신도 아니고 변경도
아니므로 "색인 이후 바뀌었다"고 보고하지 않습니다(전체 규칙은
[`skills/knowledge-fabric/references/evidence.md`](skills/knowledge-fabric/references/evidence.md)).
엑셀 숫자는 `kip xlsx-read`로 원본 셀 범위를 직접 읽습니다.

## 어디로 갈까

| 하고 싶은 일 | 문서 |
|---|---|
| 설치하고 첫 색인까지 | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) |
| 모르는 용어 찾기 | [`docs/GLOSSARY.md`](docs/GLOSSARY.md) |
| 설치 후 매일 운영 | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| 다른 조직/서버에 배포·업그레이드 | [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) |
| 승인된 목표 대비 현재 준비 상태 | [`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md) |
| 명령이 실패할 때 | [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) |

가장 쉬운 설치 방법은 AI 에이전트에게 `KIP을 셋업해줘`라고 요청하는 것입니다.
에이전트가 필요한 질문을 하나씩 묻고 설정 파일을 생성합니다.

## 구조

CLI, REST/OpenAPI, 선택형 stdio MCP는 같은 application service layer를
사용하므로 세 경로의 권한 규칙과 동작이 같습니다. 기준 런타임은 PostgreSQL
18(pgvector 포함)과 격리된 로컬 model runtime이며, PostgreSQL이 canonical
state를 소유하고 search/vector/graph projection은 언제든 다시 만들 수 있는
교체형입니다. 3.12.0부터 기본 검색은 lexical과 vector 결과를 reciprocal-rank
fusion으로 합치는 semantic search(`hybrid`)이고(ADR-065), BGE cross-encoder
reranker는 선택 사항입니다. Model runtime이 멈췄거나 projection이 아직
만들어지는 중이면 `semantic_degraded` 경고와 함께 lexical search로 동작하며,
`KIP_SEMANTIC=off`로 lexical 전용 설치를 선택할 수 있습니다. ACL 필터는 검색과
traversal보다 먼저 적용되고, ontology는 사람이 승인한 것만 사실로 취급합니다.

> **먼저 아는 편이 나은 한계**
> Slack·Apple Mail·IMAP은 환경별 reference adapter이고, Neo4j adapter는
> 존재하지 않습니다(ADR-046). 대형 corpus의 첫 embedding projection은 몇
> 시간이 걸리며 그동안 검색은 lexical로 동작합니다. 아래 검색 품질 수치는
> retrieval 근거일 뿐 answer나 ontology 품질 근거가 아닙니다.
> 전체 목록은 [10. 현재 제한 사항](#10-현재-제한-사항)에 있습니다.

---

## 목차

1. [저장소가 보장하는 것](#1-저장소가-보장하는-것)
2. [AI 에이전트 기반 설정](#2-ai-에이전트-기반-설정)
3. [설치 경로와 첫 색인](#3-설치-경로와-첫-색인)
4. [애플리케이션으로 실행](#4-애플리케이션으로-실행)
5. [Claude Code와 MCP](#5-claude-code와-mcp)
6. [실제 source 연결](#6-실제-source-연결)
7. [Ontology 검토 루프](#7-ontology-검토-루프)
8. [운영](#8-운영)
9. [배포 profile](#9-배포-profile)
10. [현재 제한 사항](#10-현재-제한-사항)
11. [검색 품질 측정 기록](#11-검색-품질-측정-기록)
12. [검증된 소스 ZIP](#12-검증된-소스-zip)
13. [라이선스와 보안](#13-라이선스와-보안)

## 1. 저장소가 보장하는 것

- 프로젝트 루트에 `AGENTS.md`와 `CLAUDE.md`가 있고, `CLAUDE.md`는 `@AGENTS.md`로
  지침을 불러옵니다.
- NAS와 connector 원본은 읽기 전용입니다. 기준 filesystem parser는 문서마다
  제한된 별도 child process에서 실행되며, 원본 권한과 network denial은 배포
  계층에서 별도로 강제합니다.
- CLI, REST, MCP는 동일한 application layer를 사용합니다.
- Search, graph, embedding은 다시 만들 수 있는 교체형 projection입니다.

승인된 목표, 현재 구현, 측정 근거, 남은 운영 격차는
[`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md)에
분리해 기록합니다. PRD/TRD의 목표 문구만 보고 현재 준비 상태를 추정하지 마세요.

## 2. AI 에이전트 기반 설정

새 배포에서 추측으로 설정 파일을 직접 편집하지 마세요. AI 에이전트에게
`KIP을 셋업해줘`라고 전달하면 `skills/kip-setup/SKILL.md`에 따라
`kip setup inspect`가 반환하는 질문(workspace, 신원 검증, 색인할 폴더의 절대
경로와 범위, classification, ACL, model egress, relation mining, secret
reference, CAS/backup 경로, 평가 dataset, reviewer)을 한 번에 하나씩 묻고,
file count·크기·확장자 분포·제외 항목·symlink를 preview로 보여줍니다. 사용자가
plan fingerprint와 read-only mount를 승인하면 `config/kip.generated.toml`
(container), `config/kip.host.generated.toml`(host/MCP),
`compose.generated.yaml`, 그리고 생성된 host config를 가리키는 `.mcp.json`을
원자적으로 생성·검증합니다.

질문, state, plan, 생성 파일에는 raw credential을 기록하지 않습니다. 런타임이
해석할 수 있는 `env:` reference만 저장하고, model credential에 한해 `file:`도
허용하며 `keychain:`과 `secret-manager:`는 거부합니다. 평가 dataset이 없으면
설치는 가능하지만 production-promoted 상태가 아니라는 제한을 receipt에 남깁니다.

검색 범위는 활성 source에 지정한 디렉터리 하위로 제한됩니다. 제거·비활성화·
범위 변경 후 서비스가 설정을 다시 읽으면 이전 색인과 알려진 ID에도 새 경계가
적용되고, 변경 범위를 다시 허용하려면 명시적인 sync가 필요합니다.

단계별 인수 절차는 [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) 2장,
명령 수준 절차는 [`docs/QUICKSTART.md`](docs/QUICKSTART.md)에 있습니다.

## 3. 설치 경로와 첫 색인

진입 경로는 두 가지입니다. **릴리스를 설치**하면 전역 `kip` 명령을 쓰고,
**저장소를 복제해 개발**하면 이 문서의 나머지 예제처럼 `./scripts/kip`를 씁니다.
다른 조직이나 저장소에서 이 패키지를 도입한다면 먼저
[`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)를 읽으세요.

### 릴리스 설치와 업데이트

한 줄 설치기는 Python보다 먼저 실행되므로 bash, curl 또는 wget, sha256sum 또는
shasum, unzip 또는 python3만 있으면 됩니다. 버전이 지정된 배포 패키지 ZIP과 그
`.sha256` sidecar를 내려받아 압축을 풀기 전에 digest를 검증하고, 값이 다르면
아무것도 풀지 않고 중단합니다. 이후 `./scripts/bootstrap.sh`를 실행하고
`./scripts/verify-package.sh`로 아카이브 전체를 다시 검증합니다.

```bash
# 설치 위치와 버전을 고정
curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh \
  | bash -s -- ~/kip --version X.Y.Z
```

`--check`, `--install-docker`, `--without-docker`는 bootstrap에 그대로
전달됩니다. `--no-bootstrap`은 압축만 풀고, `--keep-archive`는 내려받은 ZIP을
남깁니다. Windows에서는 WSL2 Ubuntu 안에서 실행합니다. 설치기는
`~/.local/bin/kip` 런처와 로그인 셸 프로필의 표시된 블록 하나를 만들어 어느
디렉터리에서나 `kip --help`, `kip doctor`, `kip setup inspect`, `kip version`,
`kip update`를 쓸 수 있게 합니다.

설치한 배포는 어디서나 `kip update`(= 배포의 `./scripts/upgrade.sh --latest`)로
올립니다. 데이터베이스 없이도 동작하고, 교체되는 것은 manifest에 있는 패키지
소유 파일뿐이라 `.env`, 생성 config, `var/`, `secrets/`, `.mcp.json`은 그대로
둡니다. git 체크아웃은 거부하므로 개발 트리는 `git pull`로 갱신합니다.

```bash
kip update --dry-run   # 계획과 두 버전 사이의 CHANGELOG만 출력
kip update             # --version X.Y.Z / --archive ZIP / --no-bootstrap
kip update --rollback  # 직전 업그레이드의 패키지 파일로 되돌리기
```

데이터베이스는 rollback 대상이 아니므로 마이그레이션을 지나는 업그레이드 전에는
`./scripts/backup.sh`를 실행합니다. 런처 위치 변경(`--bin-dir`/`KIP_BIN_DIR`),
프로필을 건드리지 않는 `--no-shell-profile`, 여러 배포를 함께 두는 경우의 규칙,
패키지/배포 소유 경계, 3.9.0·3.10.0 이전 배포의 절차,
`semantic_enabled = false`로 남은 배포가 기본 semantic search를 채택하는 방법은
[`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) 2장과 11장에 있습니다.

### 저장소에서 개발

Python·Node를 먼저 설치할 필요 없이 아래 명령부터 실행할 수 있습니다.
호환되는 기존 프로그램은 재사용하고, 없으면 프로젝트의 `var/runtime`에
Python 3.13과 Node 22를 준비합니다. 시스템 Python·Node와 셸 프로필은
수정하지 않습니다. 다운로드 연결과 최소 10GB의 여유 공간이 필요합니다.

```bash
./scripts/bootstrap.sh --check   # 설치하지 않고 준비 상태 확인
./scripts/bootstrap.sh
./scripts/app-up.sh --database-only
./scripts/kip doctor
```

스크립트는 bash를 사용하며 Windows에서는 WSL2 Ubuntu 안에서 실행해야 합니다.
Docker가 없으면 터미널에서 설치 여부를 묻습니다. 에이전트 등 비대화형
실행에서는 `--install-docker`로 시스템 Docker 설치를 명시적으로 허용하고,
외부 DB만 쓰는 CLI/MCP 설치는 `--without-docker`를 지정합니다. Bootstrap이
준비하는 고정 의존성, Kordoc OCR runtime, 격리된 model runtime
(`var/semantic-venv`)과 embedding snapshot(`var/model-cache`, 약 1.2GB),
`KIP_SEMANTIC=off`·RAM 8GiB 미만에서의 동작은
[`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) 2장 1단계에 정리돼
있습니다. Reranker는 `KIP_SEMANTIC_RERANKER=on`일 때만 약 2.3GB를 더 받습니다.

포함된 sample data를 색인하고 확인합니다.

```bash
./scripts/kip sync run --source sample
./scripts/kip search "참여율 변경 승인" --limit 10
./scripts/kip context "정산 증빙 제출기한" --limit 5
```

성공하면 결과 JSON에 `"ok": true`가 있고 `data` 배열에 항목이 하나 이상
있습니다. 비어 있으면 `./scripts/kip status`의 `data.content_units`가 0보다
큰지, `./scripts/kip doctor`에 `ok:false` 항목이 있는지 확인합니다. Sync는 새
unit을 embedding하고 projection이 완성되면 자동으로 활성화하며, 그 전까지
검색 결과의 `meta.warnings`에 `semantic_degraded`가 붙습니다. 계속 막히면
[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)를 참조하세요.

`./scripts/verify.sh`(테스트·lint·typecheck·audit)는 개발자와 릴리스용 gate이며
온보딩 단계가 아닙니다. 기여 규칙은 [`CONTRIBUTING.md`](CONTRIBUTING.md)에
있습니다.

## 4. 애플리케이션으로 실행

REST API와 worker가 필요할 때만 전체 Compose profile을 띄웁니다. CLI/MCP만
쓴다면 `--database-only`로 충분하고, 개발 중 API만 띄우려면 `./scripts/api.sh`를
씁니다.

```bash
./scripts/app-up.sh
curl http://127.0.0.1:8080/readyz
curl -sS http://127.0.0.1:8080/v1/search \
  -H "Content-Type: application/json" -H "X-KIP-API-Key: $KIP_API_KEY" \
  -d '{"query":"협약 변경 승인","limit":5}'
```

3.13.0부터 API와 worker는 PostgreSQL bootstrap role(owner)이 아니라 비
superuser·NOBYPASSRLS인 `kip_api`/`kip_worker`로 접속합니다. 이 role은 migration
직후 한 번만 도는 `roles` 서비스(`deploy/compose.roles.yaml`)가
`deploy/sql/roles.sql.template`을 적용해 만들므로 **반드시
`./scripts/app-up.sh`로 기동해야 합니다.** `docker compose -f compose.yaml`만
직접 실행하면 이 overlay가 빠져 role이 만들어지지 않고 API/worker 인증이
실패합니다. 이미 운영 중인 배포를 옮기는 절차(필요한 `.env` 항목, 확인 쿼리,
backup/restore role 조건)는
[`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) 11.8에 있습니다. Host
CLI와 MCP, 그리고 `migrate`는 계속 owner로 동작합니다.

Python client는 `sdk/python/kip_client.py`, OpenAPI는 `contracts/openapi.json`,
connector event 예제는 `examples/connector/event.json`에 있습니다. 인터넷
경계에서는 임의의 workspace·principal·ACL header를 신뢰하지 않습니다. API-key
bootstrap은 config에 고정된 단일 principal을 쓰고, 다중 사용자 배포는 검증된
JWT claim에서 workspace와 scope를 가져옵니다. 애플리케이션은 PostgreSQL이나
graph projection에 직접 연결하지 말고 CLI/REST/MCP와 같은 service layer를 써야
합니다. 자세한 내용은
[`docs/APP_INTEGRATION.md`](docs/APP_INTEGRATION.md)에 있습니다.

## 5. Claude Code와 MCP

Claude Code는 루트 `CLAUDE.md`를 읽고, 이 파일은 `AGENTS.md`를 import합니다.
프로젝트 skill은 `.claude/skills/knowledge-fabric/SKILL.md`에 있습니다. 루트
`.mcp.json`은 secret을 넣지 않고 선택형 stdio MCP adapter를 시작하며, AI 기반
setup이 이 파일을 원자적으로 갱신해 host 경로용
`config/kip.host.generated.toml`을 선택합니다(container 경로용
`config/kip.generated.toml`과 혼동하지 마세요). Tool 결과는 CLI/REST와 같은
`kip.envelope.v1`이고 MCP metadata는 권한 계약이 아닙니다. 도구 목록과 adapter
제약은 [`docs/APP_INTEGRATION.md`](docs/APP_INTEGRATION.md)에 있습니다.

MCP client 실행 환경에 `KIP_WORKSPACE`, `KIP_PRINCIPAL_ID`, `KIP_ACL_SCOPES`를
설정해 CLI/REST와 동일한 authorization context를 사용하세요. 검증된 reviewer가
ontology discovery를 검토할 때만 `KIP_ROLES=admin`을 설정합니다.

자연어 요청에서 실제 MCP 도구 선택과 근거 읽기를 점검한 결과는
[`docs/AGENT_QUALITY.md`](docs/AGENT_QUALITY.md)에 있습니다. 3.6.1에서
2026-09-10에 측정한 합성 사례 7건의 결과이며 모든 문서·모델의 답변 품질을
보장하는 수치는 아닙니다.

## 6. 실제 source 연결

`config/kip.toml`과 `.env`를 편집합니다. NAS 경로는 읽기 전용으로 mount하며
Docker app mode에서는 `KIP_NAS_PATH`가 `/sources/nas:ro`로 들어갑니다. Slack은
`KIP_SLACK_BOT_TOKEN`과 허용 conversation ID를, Apple Mail은 명시적 Automation
권한과 account/mailbox allowlist를, IMAP은 app password를 씁니다. 각 connector와
parser(HWP/HWPX 순서, PDF `pdf_inspector`/`pymupdf` backend, PPTX shape
evidence, 한국어 OCR)의 전체 설정·제한·승격 규칙은
[`docs/CONNECTORS.md`](docs/CONNECTORS.md)에 있습니다. AI가 실제 corpus를
운영한다면 루트 agent 지침과 contract 문서 다음으로
[`docs/AI_OPERATOR_RUNBOOK.md`](docs/AI_OPERATOR_RUNBOOK.md)를 읽어야 합니다.

기존 index는 전체 source sync 없이 평가하고 승격합니다. `reextract`는
non-mutating shadow 작업이고, `--activate`가 revision·hash·ACL·classification·
quality를 확인한 뒤 문서별로 이전 extraction을 보존하면서 active state를
교체합니다. 기본 대상은 HWP/HWPX이므로 PDF 등은 `--extension`으로 지정합니다.

```bash
./scripts/kip parser reextract --source SOURCE_NAME
./scripts/kip parser reextract --source SOURCE_NAME --activate
./scripts/kip parser reextract --source SOURCE_NAME --extension .pdf
```

## 7. Ontology 검토 루프

Model, parser, relation-miner 출력은 사람이 승인하기 전까지 candidate이며 어떤
결과도 사실로 조용히 승격하지 않습니다. Relation candidate는 승인된 entity만
참조하므로 mine → entity 승인 → 다시 mine → relation 승인 순서로 진행합니다
(entity 승인이 mining digest를 바꾸므로 두 번째 실행은 실제 재평가입니다).
승인된 entity는 search alias를 확장하고, 승인 assertion은 exact evidence와 함께
`kip answer`/`kip context`에 들어가며 source가 바뀌면 자동으로 제외됩니다.
동일 기능은 REST와 MCP에도 있고, 자세한 내용은
[`docs/ONTOLOGY_GUIDE.md`](docs/ONTOLOGY_GUIDE.md)에 있습니다.

```bash
./scripts/kip ontology mine --unit-id UNIT_ID
./scripts/kip review list
./scripts/kip review approve CANDIDATE_ID
./scripts/kip review revoke ASSERTION_ID --note "reason"
```

## 8. 운영

`GET /readyz`는 실제 DB round-trip을 수행하고 production compose healthcheck가
이를 사용합니다(`/healthz`는 liveness 전용). `./scripts/ops-report.sh`는 failed
job, queue age, 마지막 성공 sync, disk free, backup age, API health를 한 번에
확인하고 `--json`과 `KIP_OPS_WEBHOOK` 알림을 지원합니다.
`./scripts/backup.sh --retain N`은 seal과 checksum이 있는 backup을 만들고,
`./scripts/install-launchd.sh`(Linux는 `deploy/systemd/kip-semantic.service`)는
daily backup·periodic sync·model runtime 상시 실행을 등록합니다. 삭제된 파일은
`[sync] deletion_grace_scans`(기본 2)번의 연속된 complete scan에서 계속 없을
때만 tombstone되며 failed/empty scan은 삭제 근거가 아닙니다. 세부 절차는
[`docs/OPERATIONS.md`](docs/OPERATIONS.md)와
[`docs/AI_OPERATOR_RUNBOOK.md`](docs/AI_OPERATOR_RUNBOOK.md)에 있습니다.

## 9. 배포 profile

| Profile | 구성 |
|---|---|
| 최소 | PostgreSQL, filesystem source, lexical search(`KIP_SEMANTIC=off`), CLI |
| 표준 | 최소 profile + 로컬 semantic search(model runtime), API, worker, HWP broker, 선택형 Slack/Mail connector |
| 확장 | 표준 + opt-in 관계 추출. 검토는 CLI/API로 제공 |

## 10. 현재 제한 사항

이 저장소는 바로 배포할 수 있는 패키지이지 모든 production adapter가 완성됐다는
주장이 아닙니다. Filesystem, text, PDF, XLSX shallow/deep, memory repository,
CLI/API contract, PostgreSQL migration, pgvector projection, stdio MCP adapter는
구현돼 있습니다.

- Slack, Apple Mail, IMAP은 환경별 reference adapter입니다.
- Neo4j adapter는 존재하지 않습니다(ADR-046). Graph traversal은 활성 repository
  backend 안에서 동작하며, 도입 게이트를 통과하면 그때 전용 port와 함께 read
  projection으로 추가될 수 있습니다.
- 로컬 semantic path는 3.12.0부터 기본 경로이며, release가 검토한 embedding
  identity의 projection은 완성되면 자동 활성화됩니다. 대형 corpus의 첫
  projection은 오래 걸립니다. 기준 Apple Silicon(24GB)에서 약 176,500 unit의
  1,912-file OneDrive corpus는 약 3시간이 필요했고 그동안 검색은 lexical로
  동작했습니다. 검토되지 않은 custom embedding identity는 자동 활성화되지 않고
  평가 뒤 `kip projection activate`가 필요합니다. 지원하는 PostgreSQL profile은
  semantic search를 끈 배포에서도 pgvector와 1024d HNSW index를 포함합니다.
- 변경되지 않은 revision은 parser version이 바뀔 때 명시적 shadow/activate
  re-extraction을 사용합니다. 모든 format을 한 번에 강제로 다시 색인하는 일반
  명령은 제공하지 않습니다.
- Lexical candidate set에 없는 문서는 reranking으로 복구할 수 없습니다.
  Starter lexical path는 ACL-filtered candidate 최대 40개를 candidate-local
  BM25로 rerank하며 RapidFuzz 3.14.6을 fallback으로 씁니다. 3.12.0은 흔한 query
  n-gram을 candidate matching에서 제외합니다(BM25 reranker는 전체 질문을 계속
  채점). 기준은 `max(200, 전체 unit 수 * search.lexical_common_term_fraction)`
  (fraction 기본 0.02)이라 10,000 unit 미만 corpus에서는 200-unit floor가 실제
  기준이고, 전체 unit이 약 400개 미만이면 probe를 건너뜁니다.
- Dependency PR과 parser/model upstream 알림은 candidate discovery일 뿐 자동
  활성화가 아닙니다. Parser/model 교체는 shadow 평가와 승격 결정을 거칩니다.

수정이나 배포 전에 `./scripts/verify.sh`를 실행하세요. 필수 검사 도구가 없으면
실패하므로 `./scripts/bootstrap.sh`로 환경을 복구한 뒤 다시 실행합니다.

## 11. 검색 품질 측정 기록

아래 수치는 모두 **retrieval 근거**이지 answer 또는 ontology 품질 근거가
아닙니다. 서로 다른 시점의 corpus와 설정에서 나온 값이므로 한 표 안에서
비교하지 마세요.

### 현재 릴리스 측정 (3.12.0)

검토된 private 19-case, 1,912-file OneDrive corpus(약 176,500 unit),
space `qwen3-embedding-0.6b-1024-c4000-ht1`, 기준 Mac, warmup 1회.

| Variant | Recall@10 | MRR | P95 |
|---|---:|---:|---:|
| hybrid (기본) | 89.5% | 89.5% | 2.42 s |
| lexical (BM25 rerank, common-term pruning) | 89.5% | 63.8% | 2.22 s |
| vector | 89.5% | 83.3% | 0.08 s |
| reranked, BGE, 20 candidates | 94.7% | 85.3% | 7.20 s |

같은 19-case의 lexical mode는 common-term pruning **이전**에 78.9% / 58.3%,
P95 11.11 s였습니다. `hybrid`가 기본이 된 이유는 fused 결과를 BM25로 다시
rerank하면 78.9% / 59.8%로 떨어졌고, BGE cross-encoder는 recall은 올렸지만
순위 품질은 올리지 못하면서 지연이 3-4배였기 때문입니다(그래서 선택 사항).
모든 variant에서 실패 사례와 ACL 누출은 0이었습니다.

**19건은 작은 집합입니다. 한 건이 점수를 5.3 포인트 움직입니다.**

### 과거 측정 (다른 corpus·설정)

- **2026-08-13 historical `c4000` shadow run** — 당시 활성 30,565 unit 기준이며
  3.12.0의 lexical/fusion 변경 이전입니다. lexical `0.789/0.646`, vector HNSW
  `0.947/0.822`(P95 133.75 ms), hybrid `0.895/0.702`, reranked `0.842/0.656`,
  ACL 누출 0. 당시 결정은 `keep_disabled`였습니다.
- **ADR-034 (2026-08-11) lexical reranker 승격** — 같은 19-case의 당시 corpus에서
  RapidFuzz `0.737/0.576`, candidate-local BM25 `0.789/0.646`. 이 비교가 BM25를
  기본 lexical reranker로 승격한 근거이고 RapidFuzz는 fallback으로 남습니다.
- **공개 government pilot (36 cases, PDF 6건)** — lexical·hybrid 모두 Recall@10
  100%, MRR 98.6%. 집합이 포화 상태라 mode를 구분하지 못합니다.

전체 결과, latency, fingerprint, 개선 이력은
[`docs/RAG_EVALUATION.md`](docs/RAG_EVALUATION.md),
[`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md),
[`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md),
`evaluation/reports/`에 있습니다. 라이선스가 명확한 한국어 공개 corpus로 직접
재현하는 절차(`make fetch-corpus` → `make evaluate`)는
[`docs/QUICKSTART.md`](docs/QUICKSTART.md)의 "공개 corpus로 RAG 평가 재현하기"
절에 있고, 전체 corpus의 parser·retrieval·semantic·graph·ontology audit는
[`2026-08-06 기록`](https://github.com/64etuor/kip/blob/1b04bad685762fe3002d9c4ec6a75f267df9fb94/docs/RAG_QUALITY_AUDIT_2026-08-06.md)에
있습니다.

## 12. 검증된 소스 ZIP

인터넷 연결 환경에 소스와 필수 운영 문서만 전달하려면 작업 디렉터리를 직접
압축하지 말고 결정적 배포 패키지 ZIP을 만듭니다.

```bash
./scripts/build-package.sh
./scripts/verify-package.sh dist/kip-$(cat VERSION).zip
```

ZIP은 하나의 버전 디렉터리 아래에 구현 코드, 잠긴 의존성, 테스트, migration,
contract, ontology, 예제, 자동화, canonical 운영 문서를 담고, 로컬 설정,
credential, DB, CAS/output, private 평가 자료, 내부 plan, 생성형 package metadata,
릴리스 바이너리는 제외합니다. `KIP-MANIFEST.json`, 내부 `SHA256SUMS`,
외부 `.zip.sha256` 파일로 전달물을 독립적으로 검증할 수 있습니다. 정식 릴리스는
clean tree에서 만들고, `--allow-dirty`는 이름이 명확한 로컬 candidate에만 씁니다.
GitHub 릴리스에는 이 ZIP과 `.sha256`, `install.sh`가 함께 게시되므로 내려받아
직접 검증·압축 해제하는 경로도 그대로 유효합니다.

## 13. 라이선스와 보안

KIP 자체는 MIT License입니다. 전문은 [`LICENSE`](LICENSE)에 있습니다.

KIP는 서드파티 구성요소를 사용하며, **그중 두 개는 copyleft라 배포 방식에
따라 의무가 발생합니다.** 전체 목록과 각 구성요소의 라이선스는
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)에 있고, 특정 릴리스의
정확한 내역은 `make release`가 생성하는 SPDX SBOM이 기준입니다.

| 구성요소 | 라이선스 | 왜 중요한가 |
|---|---|---|
| PyMuPDF | **AGPL-3.0** 또는 Artifex 상용 | `requirements/runtime.txt`에 pin되어 **프로덕션 이미지에 포함**되고, 기본 PDF 경로의 표 fallback이 호출합니다 (ADR-054). AGPL은 네트워크 copyleft라 서비스로 제공할 때 해당 구성요소에 대한 소스 제공 의무가 따릅니다 |
| psycopg, psycopg-pool | **LGPL-3.0-only** | 공개 API를 그대로 사용하므로 KIP에 전이되지 않지만, 재배포 시 LGPL 고지 의무가 있습니다 |

나머지 의존성은 MIT, BSD, Apache-2.0, PostgreSQL License입니다. PyMuPDF 없이
배포하려면 `[parsers.pdf] tables_enabled = false`로 두고 `extractors` extra를
제외한 뒤 runtime lock을 다시 생성하세요. PDF 기본 백엔드 `pdf-inspector`는
MIT이므로 표 fallback만 사라집니다. 실제 의무 판단은 법률 자문을 받으시기
바랍니다. 이 문서는 사실 목록이지 법률 자문이 아닙니다.

권한 경계, database role, ACL 강제, 위협 모델은
[`docs/SECURITY.md`](docs/SECURITY.md)에 있습니다. 기여 절차와 필수 gate는
[`CONTRIBUTING.md`](CONTRIBUTING.md), 영어 요약은
[`README.en.md`](README.en.md)를 참조하세요.
