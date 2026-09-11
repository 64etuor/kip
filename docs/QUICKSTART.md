# Quickstart

> 처음 보는 단어가 나오면 [`GLOSSARY.md`](GLOSSARY.md)(용어집)를 먼저 보세요.
> ACL, 온톨로지, 프로젝션 같은 말이 한 줄씩 쉬운 말로 정리되어 있습니다.

처음 복제하거나 제3자 환경에 적용한다면 명령을 실행하기 전에
[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md)의 데이터 경계, ACL, 외부
전송, 품질 기준 결정을 먼저 완료한다.

가장 안전한 진입점은 AI agent에게 “KIP을 셋업해줘”라고 요청하는 것이다.
agent는 `kip-setup` Skill에 따라 매번 하나의 누락된 결정만 질문하고, CLI가
수집 범위를 미리 계산한 뒤 승인된 plan만 원자적으로 적용한다.

가장 빠른 설치 경로는 릴리스에 게시된 한 줄 설치기다. 저장소 복제 없이 최신
배포 패키지를 내려받아 digest를 검증한 뒤 `$KIP_HOME` 또는 `~/kip`(비어 있거나
아직 없는 디렉터리)에 풀고 bootstrap까지 실행한다.

```bash
curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash
# 위치와 버전 고정: | bash -s -- ~/kip --version 3.12.0
```

bash, curl 또는 wget, sha256sum 또는 shasum, unzip 또는 python3만 있으면 되고
Python은 bootstrap이 준비한다. digest가 맞지 않으면 아무것도 풀지 않는다.
`--check`/`--install-docker`/`--without-docker`는 bootstrap으로 전달되고
`--no-bootstrap`은 압축만 푼다. 같은 명령을 기존 패키지 배포 디렉터리에 실행하면
그 자리에서 업그레이드한다. 배포 안의 `./scripts/upgrade.sh --latest`(`kip
update`)도 같은 패키지를 적용하지만, 런처는 이미 그 배포를 가리킬 때만 새로
쓰고 셸 프로필은 건드리지 않는다(3.12.1).

설치기는 전역 `kip` 런처를 `~/.local/bin/kip`에 쓰고(`--bin-dir DIR` 또는
`KIP_BIN_DIR`로 변경) 로그인 셸 프로필(zsh는 `~/.zshrc`, bash는 Linux
`~/.bashrc`·macOS `~/.bash_profile`, 그 외 `~/.profile`)에
`# >>> KIP >>>` … `# <<< KIP <<<` 블록 하나를 추가해 `KIP_HOME`을 내보내고
런처 디렉터리를 `PATH`에 넣는다. 설치기를 실행할 때마다 블록을 통째로
교체하며 그 밖의 내용은 건드리지 않고, `--no-shell-profile`로 끌 수 있다. 셸을 다시 시작하거나 프로필을
`source`하면 어느 디렉터리에서나 아래 명령을 쓸 수 있다.

```bash
kip --help
kip doctor
kip setup inspect
kip version
kip update          # = ./scripts/upgrade.sh --latest
kip update --dry-run   # --version X.Y.Z / --archive ZIP / --rollback / --no-bootstrap
```

`kip update`는 데이터베이스 없이도 동작하고, git 체크아웃은 거부하므로 개발
트리는 `git pull`로 갱신한다. 아래 예제처럼 저장소에서 직접 작업할 때는 계속
`./scripts/kip`를 쓴다.

저장소를 복제해 개발하는 경우에는 `./scripts/kip`가 `.venv`를 요구하므로 먼저
`./scripts/bootstrap.sh`를 실행한다. 필요한 Python·Node는 프로젝트 안에
준비한다. Docker 시스템 설치를 허용하려면 `--install-docker`, 외부 DB만
사용하면 `--without-docker`를 붙인다. `--check`는 읽기 전용 준비 점검이다.

```bash
./scripts/bootstrap.sh
./scripts/kip setup inspect
./scripts/kip setup answer --question workspace --value acme-rnd
# inspect와 answer를 complete=true까지 반복
./scripts/kip setup preview
./scripts/kip setup plan --output .kip/setup-plan.json
# plan의 scope, read-only mounts, egress, relation mining 선택, warnings를 사람이 승인
./scripts/kip setup apply --plan .kip/setup-plan.json
./scripts/kip setup verify --plan .kip/setup-plan.json
```

`setup apply`는 설정 파일을 생성하고 `verify`는 파일과 runtime 준비를 검증한다.
실제로 서비스가 뜨고 문서가
색인되려면 receipt의 `next_steps`를 순서대로 실행해야 한다.

```bash
./scripts/app-up.sh --database-only
./scripts/kip sync run --source SOURCE
./scripts/kip search "스모크 테스트 질의" --limit 5
./scripts/kip read UNIT_ID
```

`./scripts/app-up.sh --database-only`는 CLI/MCP에 필요한 DB 준비와 migration만
수행한다. generated 배포에서는 승인된 `postgres` 서비스만 기동해 준비를 기다린
뒤 `config/kip.host.generated.toml`이 같은 plan과 database secret ref를
가리키는지 확인하고 host migration을 실행한다. DB credential만 필요하고
API/worker/identity credential은 읽지 않는다. external DB면 Docker 없이
migration만 한다. API/worker 이미지는 빌드하지 않는다. Host model runtime
(`var/semantic-venv`)이 설치돼 있고 `KIP_SEMANTIC=off`가 아니면 이 명령이
`./scripts/semantic-server.sh start`로 runtime을 띄우고 준비될 때까지 기다린다.
시작하지 못해도 실패하지 않고 경고만 남기며 검색은 lexical로 동작한다.

Setup plan은 이 runtime이 있는지를 `semantic_search`로 기록하고 그에 맞는
host/container config를 생성한다. Lexical 전용 plan은 compose의 `models` 서비스를
빼고 그 이유를 경고한다. 첫 sync는 새 unit을 embedding하며, 검토된 기본 embedding
identity의 projection이 완성되면 자동으로 활성화한다. 그 전까지 검색 envelope의
`meta.warnings`에 `semantic_degraded`가 붙고 결과는 lexical이다. 큰 corpus의 첫
projection은 몇 시간이 걸릴 수 있다. `./scripts/kip doctor`의 `semantic_search`
항목이 runtime 연결과 projection 완성도(`stale` 포함), 고칠 명령을 알려준다.
기본 검색 mode는 `hybrid`(lexical+vector reciprocal-rank fusion)이고 runtime은
embedding model만 load한다. BGE reranker는 선택 사항이다
(`models.reranker.backend = "http"`, `KIP_SEMANTIC_RERANKER=on`).

REST API나 worker가 필요하면 전체 `./scripts/app-up.sh`를 실행한다.
`compose.generated.yaml`과 `config/kip.generated.toml`이 있으면 standalone
generated Compose만 선택하여 승인된 read-only source mount, CAS 경로, 생성
config를 적용한다. DB 준비와 migration 후 서비스가 시작된다. 둘 다 없으면
안내와 함께 기본 app profile로 동작하고 하나만 있으면 불완전한 설정으로
실패한다. `./scripts/app-up.sh --down`으로 종료한다. Model runtime은 machine당
하나만 뜬다. amd64에서는 compose `models` 서비스가 loopback에 publish되어 host
CLI/MCP도 쓰고, ARM이거나 host runtime이 이미 응답하면 host runtime을 쓰며 이때
API/worker container는 lexical 검색(`semantic_degraded`)으로 동작한다.

실제 credential 대신 `env:KIP_DATABASE_URL` 같은 secret reference만 답한다.
런타임은 `env:`와 (모델 credential에 한해) `file:` reference만 해석하며,
`keychain:`/`secret-manager:`는 CLI가 거부한다. `/`, 홈 디렉터리, 프로젝트
루트 또는 그 상위 디렉터리는 source root로 거부된다. 생성된 config와 Compose
파일은 로컬 전용이며 Git에서 제외된다. source 질문에는 폴더 절대경로만
답할 수도 있다. preview의 ACL/분류와 local/cloud-only 건수를 확인한다.
cloud-only 파일은 provider 앱에서 선택하여 다운로드한 뒤 수집한다.

## Local CLI profile

```bash
./scripts/bootstrap.sh
./scripts/app-up.sh --database-only
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
| `semantic_projection_status` (`capabilities`) | 의미 기반 검색 상태. 첫 sync가 embedding을 마치면 `active`가 됩니다. 그 전(`missing`/`shadow`)에는 검색이 lexical로 동작하고, lexical 전용 설치(`KIP_SEMANTIC=off`)는 `disabled`입니다. 빠른 identity 확인이라 빠진 unit(`stale`)은 `kip doctor`나 `kip projection verify`로 확인합니다 |
| `ok` / `reason` (`doctor`) | 각 점검의 통과 여부와, 실패 시 해야 할 일 |
| `failed` / `warnings` (`sync run`) | 읽지 못한 파일 수와 파일별 이유 |

막히면 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)에서 증상별 해결 순서를
찾을 수 있습니다.

## Application profile

```bash
./scripts/app-up.sh
curl http://127.0.0.1:8080/readyz
```

API와 worker가 필요할 때만 전체 프로파일을 띄운다. CLI/MCP만 쓰면
`./scripts/app-up.sh --database-only`로 충분하다. `app-up.sh`는 guided setup의
standalone Compose와 secret references를 함께 해석한다. 직접 base Compose와 합치면 승인되지 않은 mount가 추가될 수 있으므로
생성 배포는 wrapper로 실행한다.

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

The distributed configuration keeps the public corpus disabled; semantic
search (`hybrid`) and the pinned embedding model are on by default (ADR-065);
the BGE reranker is opt-in. To
reproduce the checked-in pilot, set `enabled = true` for `public-government`
in `config/kip.toml`. Bootstrap already installed the model runtime unless
`KIP_SEMANTIC=off` was set; `./scripts/bootstrap-semantic.sh` installs or
repairs it.

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

With the pinned default embedding identity, sync embeds the corpus and a
complete projection activates itself (`search.semantic_auto_activate`). A
custom embedding identity stays in shadow mode; see `docs/RAG_EVALUATION.md`
before considering `projection activate --report REPORT --candidate VARIANT`.
