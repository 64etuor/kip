# Quickstart

> 처음 보는 단어가 나오면 [`GLOSSARY.md`](GLOSSARY.md)(용어집)를 먼저 보세요.
> ACL, 온톨로지, 프로젝션 같은 말이 한 줄씩 쉬운 말로 정리되어 있습니다.

처음 복제하거나 제3자 환경에 적용한다면 명령을 실행하기 전에
[`STARTER_KIT_GUIDE.md`](STARTER_KIT_GUIDE.md)의 데이터 경계, ACL, 외부
전송, 품질 기준 결정을 먼저 완료한다.

가장 안전한 진입점은 AI agent에게 “KIP을 셋업해줘”라고 요청하는 것이다.
agent는 `kip-setup` Skill에 따라 매번 하나의 누락된 결정만 질문하고, CLI가
수집 범위를 미리 계산한 뒤 승인된 plan만 원자적으로 적용한다.

새로 복제한 저장소에서는 `./scripts/kip`가 `.venv`를 요구하므로 먼저
`./scripts/bootstrap.sh`를 실행한다(Python 3.12+ 필요).

```bash
./scripts/bootstrap.sh
./scripts/kip setup inspect
./scripts/kip setup answer --question workspace --value acme-rnd
# inspect와 answer를 complete=true까지 반복
./scripts/kip setup preview
./scripts/kip setup plan --output .kip/setup-plan.json
# plan의 scope, read-only mounts, egress, warnings를 사람이 승인
./scripts/kip setup apply --plan .kip/setup-plan.json
./scripts/kip setup verify --plan .kip/setup-plan.json
```

`setup apply`와 `verify`는 설정 파일만 생성한다. 실제로 서비스가 뜨고 문서가
색인되려면 receipt의 `next_steps`를 순서대로 실행해야 한다.

```bash
./scripts/migrate.sh
./scripts/app-up.sh
./scripts/kip sync run --source SOURCE
./scripts/kip search "스모크 테스트 질의" --limit 5
```

`./scripts/app-up.sh`는 `compose.generated.yaml`과
`config/kip.generated.toml`이 있으면 base `compose.yaml` 위에 override를
겹쳐 승인된 read-only source mount, CAS 경로, 생성 config를 컨테이너에
적용한다. override가 없으면 안내 문구와 함께 기본 app profile로 동작한다.
`./scripts/app-up.sh --down`으로 종료한다.

실제 credential 대신 `env:KIP_DATABASE_URL` 같은 secret reference만 답한다.
런타임은 `env:`와 (모델 credential에 한해) `file:` reference만 해석하며,
`keychain:`/`secret-manager:`는 CLI가 거부한다. `/`, 홈 디렉터리, 프로젝트
루트 또는 그 상위 디렉터리는 source root로 거부된다. 생성된 config와 Compose
override는 로컬 전용이며 Git에서 제외된다.

## Local CLI profile

```bash
cp .env.example .env
cp config/kip.example.toml config/kip.toml
./scripts/bootstrap.sh
./scripts/dev-up.sh
./scripts/migrate.sh
./scripts/kip sync run --source sample
./scripts/kip search "참여율 변경" --limit 10
./scripts/kip xlsx-read --artifact-id ARTIFACT_ID --sheet "정산" --range "A1:F40"
```

Reference 설정은 filesystem parser를 파일 하나당 bounded child에서 직렬
실행하도록 `[parsers.isolation]`을 활성화한다. 이 설정을 끄는 것은 raw/isolated
개발 비교에만 사용한다. NAS/OneDrive 운영에서는 source를 별도로 read-only로
마운트해야 하며, 검색과 `xlsx-read`는 parser child를 실행하지 않는다.

For a real read-only OneDrive audit, use [`docs/AI_OPERATOR_RUNBOOK.md`](AI_OPERATOR_RUNBOOK.md); it defines parser comparison, source-grounded validation, A/B scoring, and the post-fix cycle.

## 어떤 명령을 쓸까 (Which command?)

이름이 비슷한 네 명령의 차이입니다.

| 명령 | 언제 쓰나 | 돌려주는 것 |
|---|---|---|
| `search` | 키워드로 관련 문서를 찾을 때 | 문서 조각 목록(발췌 + 원문 위치) |
| `context` | AI에게 넘길 근거 묶음이 필요할 때 | 크기 제한이 걸린 근거 묶음 |
| `answer` | 질문에 대한 답을 근거와 함께 받고 싶을 때 | 답변 + 인용(근거 부족하면 거부) |
| `read` | 검색 결과 하나의 원문을 그대로 볼 때 | 그 조각의 전체 원문 |

발췌(snippet)만 보고 판단하지 말고, 중요한 내용은 `read`로 원문을 확인하세요.
엑셀 숫자는 `xlsx-read`로 원본 셀 범위를 직접 읽어야 합니다.

## 명령 출력 읽는 법 (Reading the output)

모든 출력은 JSON이며 `"ok": true`면 성공, `"error"`가 있으면 실패입니다.
자주 보는 필드만 정리하면:

| 필드 (명령) | 뜻 |
|---|---|
| `content_units` (`status`) | 검색 가능한 문서 조각 수. 0이면 아직 색인되지 않은 것 |
| `source_objects` (`status`) | 수집된 원본 파일 수 |
| `assertion_candidates` (`status`) | 사람 검토를 기다리는 관계 후보 수(0이면 할 일 없음) |
| `lexical_search` (`capabilities`) | 키워드 검색 사용 가능 여부 |
| `semantic_projection_status` (`capabilities`) | 의미 기반 검색 상태. `disabled`가 기본이며 정상입니다 |
| `ok` / `reason` (`doctor`) | 각 점검의 통과 여부와, 실패 시 해야 할 일 |
| `failed` / `warnings` (`sync run`) | 읽지 못한 파일 수와 파일별 이유 |

막히면 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)에서 증상별 해결 순서를
찾을 수 있습니다.

## Application profile

```bash
./scripts/app-up.sh
curl http://127.0.0.1:8080/readyz
```

`app-up.sh`는 guided setup이 적용된 저장소에서는
`docker compose -f compose.yaml -f compose.generated.yaml --profile app up -d --build`
를, 그렇지 않으면 `docker compose --profile app up -d --build`를 실행한다.

The API and CLI call the same service layer. App integrations should use REST/OpenAPI unless the calling system specifically supports MCP.

Push a change from a custom application connector:

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  --data-binary @examples/connector/event.json
```

API-key mode always uses the principal, workspace, and scopes from approved
configuration. Do not send identity or ACL headers. In multi-user production,
set `identity.mode = "proxy_jwt"`, install `.[identity]`, and use
`Authorization: Bearer ...`; the proxy JWT must include the configured identity
and fresh ACL-snapshot claims.

## Licensed public RAG evaluation

The distributed configuration keeps the public corpus and semantic models
disabled. To reproduce the checked-in pilot, set `enabled = true` for
`public-government`, `models.embedding`, and `models.reranker` in
`config/kip.toml`, while leaving `search.semantic_enabled = false`.

```bash
make fetch-corpus
./scripts/fetch_public_corpus.py --check
./scripts/kip sync run --source public-government
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
```

In another terminal:

```bash
./scripts/semantic-smoke.sh
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
make evaluate
```

The semantic projection stays in shadow mode. See `docs/RAG_EVALUATION.md`
before considering `projection activate --report REPORT --candidate VARIANT`.
