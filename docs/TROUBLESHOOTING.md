# 문제 해결 (Troubleshooting)

막혔을 때 여기서 증상을 찾아 순서대로 따라 하세요. 용어가 낯설면
[`GLOSSARY.md`](GLOSSARY.md)를 먼저 보세요.

**설치 전이나 Python·Node·Docker 문제라면 먼저 실행하세요.**

```bash
./scripts/bootstrap.sh --check
```

설치가 끝난 뒤 자료 조회 문제라면 다음 두 개를 실행하세요.

```bash
./scripts/kip doctor    # 설정·저장소·폴더·OCR 상태를 점검하고 해야 할 일을 알려줍니다
./scripts/kip status    # 실제로 몇 건이 색인되어 있는지 보여줍니다
```

`doctor` 출력에서 `"ok": false`인 항목의 `reason`이 다음에 할 일입니다.

---

## 1. 설치가 안 될 때

### 한 줄 설치기가 checksum 불일치로 중단 (`nothing was extracted`)
내려받은 ZIP이 `.sha256` sidecar의 digest와 다르거나 sidecar 형식이 잘못된
경우입니다. 검증은 압축을 풀기 전에 하므로 대상 디렉터리는 그대로입니다.
중단된 다운로드가 흔한 원인이니 같은 명령을 다시 실행하고, 버전을 고정했다면
그 릴리스에 ZIP과 `.sha256`이 모두 있는지 확인하세요.

```bash
curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh \
  | bash -s -- ~/kip --version 3.9.0
```

계속 실패하면 두 자산을 직접 내려받아
`shasum -a 256 -c kip-X.Y.Z.zip.sha256`으로 확인하세요.

### `... is not empty and is not a KIP deployment`
새로 설치할 디렉터리는 비어 있거나 아직 없어야 합니다. 기존 배포 위에 실행할
때는 `VERSION`과 `KIP-MANIFEST.json`(3.10.0 이전 배포는
`STARTER-KIT-MANIFEST.json`)이 있어야 제자리 업그레이드로 넘어갑니다. 다른 경로를 지정하거나(`bash -s -- ~/kip2`) 기존 내용을 먼저
옮기세요. 설치기는 어떤 파일도 지우지 않습니다.

### 설치는 끝났는데 `kip: command not found`
설치기는 `~/.local/bin/kip` 런처를 쓰고(`--bin-dir DIR` 또는 `KIP_BIN_DIR`로
위치를 바꿉니다) 로그인 셸 프로필에 표시된 블록 하나를 추가합니다. zsh는
`~/.zshrc`, bash는 Linux에서 `~/.bashrc`·macOS에서 `~/.bash_profile`, 그 밖의
셸은 `~/.profile`입니다.

```sh
# >>> KIP >>>
export KIP_HOME='/home/kip/kip'
KIP_BIN='/home/kip/.local/bin'
case ":$PATH:" in *":$KIP_BIN:"*) ;; *) export PATH="$KIP_BIN:$PATH" ;; esac
unset KIP_BIN
# <<< KIP <<<
```

경로는 설치 시점의 절대 경로가 인용부호로 감싸여 들어갑니다.

블록은 `KIP_HOME`을 내보내고 런처 디렉터리를 `PATH`에 넣을 뿐이며, 재설치나
업그레이드 때 통째로 교체되므로 중복되지 않습니다. 프로필의 다른 내용은
바뀌지 않습니다. 현재 셸에는 아직 반영되지 않았을 뿐이니 순서대로 확인하세요.

1. 셸을 새로 열거나 프로필을 다시 읽습니다: `source ~/.zshrc`(쓰는 프로필로).
2. 런처가 있는지 봅니다: `ls -l ~/.local/bin/kip`.
3. `--no-shell-profile`로 설치했거나 fish 등 다른 셸을 쓴다면 프로필이 바뀌지
   않았습니다. 해당 셸의 설정에 직접 넣으세요.

```bash
export KIP_HOME=~/kip
export PATH="$HOME/.local/bin:$PATH"
```

런처는 `KIP_HOME`(없으면 설치 시점의 배포 경로)의 `scripts/kip`를 실행합니다.
배포를 옮겼거나 여러 개를 쓴다면 `KIP_HOME`으로 대상을 고르고, 저장소
체크아웃에서는 `./scripts/kip`를 씁니다.

### `KIP runtime is not installed at ...; Run: .../scripts/bootstrap.sh`
`--no-bootstrap`으로 설치했거나 bootstrap이 중단돼 배포에 `.venv`가 없습니다.
런처와 `./scripts/kip`는 시스템 Python으로 대신 실행하지 않고 종료 코드 69로
멈춥니다. 안내된 `scripts/bootstrap.sh`를 실행한 뒤 다시 시도하세요.

### `... is a git checkout; update it with git pull`
저장소를 복제해 쓰는 개발 트리입니다. 설치기와 `upgrade.sh`, `kip update`는
git 트리를 건드리지 않습니다.

```bash
git pull
./scripts/bootstrap.sh
```

### `... predates the in-place upgrader`
3.9.0 이전에 만들어진 배포에는 `scripts/upgrade.sh`가 없습니다.
[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) 11.5의 수동 절차를 한 번 수행하면
그 뒤부터는 설치기와 `./scripts/upgrade.sh`로 올릴 수 있습니다.

### 업그레이드가 `Action required`와 exit 75로 끝남
새 패키지 파일은 적용됐지만 데이터베이스에 연결할 수 없어 마이그레이션이 실행되지
않은 상태입니다. DB를 올린 뒤 나머지를 직접 실행하세요.

```bash
./scripts/app-up.sh --database-only
./scripts/migrate.sh
./scripts/kip doctor
```

### `./scripts/bootstrap.sh`가 Python 버전 오류로 멈춤
새 환경에서는 `./scripts/bootstrap.sh`가 Python 3.12+를 준비합니다.
기존 `.venv`가 손상됐거나 구버전이면 덮어쓰지 않습니다. 기존 환경을
다른 이름으로 보관한 뒤 재실행하세요. 명시한 `KIP_PYTHON`이 잘못됐으면
그 경로를 먼저 수정해야 하며 자동으로 다른 인터프리터를 고르지 않습니다.

```bash
mv .venv ".venv.backup.$(date +%s)"   # 기존 환경이 있을 때만
./scripts/bootstrap.sh
```

### `Kordoc OCR requires Node.js 20.9+` (bootstrap 또는 doctor 실패)
Kordoc OCR 런타임은 Node.js 20.9 이상을 요구합니다. `doctor.sh`의
`Node 20.9+ for Kordoc OCR` 항목도 같은 이유로 실패합니다.

```bash
./scripts/bootstrap.sh
```

### 설치기가 `Action required`로 멈춤
Docker가 없으면 `./scripts/bootstrap.sh --install-docker`로 설치를 허용할 수
있습니다. 관리자 인증은 대화형 터미널에서 진행하세요. Docker Desktop 창의
약관·첫 실행 설정이 끝나지 않았거나 Docker Engine 권한이 없으면 준비 완료로
처리하지 않습니다. Linux의 Docker 그룹 권한은 root와 동등한 접근을 주므로
관리자와 결정하며, 새 로그인 세션이 필요할 수 있습니다. 기존 원격 context는
바꾸지 않으므로 해당 엔진의 연결을 복구한 뒤 같은 명령을 다시 실행하세요.
WSL2는 Windows Docker Desktop 설치와 해당 배포판의 WSL integration을
완료해야 합니다. 외부 DB만 쓰는 경우에만 `--without-docker`로 건너뜁니다.

### 다운로드 checksum 오류 또는 `var/runtime` 충돌
검증에 실패한 다운로드는 실행하지 않습니다. 네트워크/배포 pin을 확인하고
다시 실행하세요. 기존의 불완전한 버전 디렉터리나 symlink가 충돌하면 먼저
내용을 보관·확인해야 하며, 설치기는 임의로 삭제하거나 외부 경로에 쓰지 않습니다.

### `Kordoc lock validation failed` 또는 npm audit 실패
`./scripts/audit-kordoc.sh`가 실패한 것입니다. 앞의 메시지는
`requirements/kordoc/package.json`과 `package-lock.json`이 어긋났다는 뜻이므로
lock을 되돌리거나 manifest와 함께 다시 생성합니다. 뒤이은 `npm audit` 실패는
high 이상 advisory이거나 registry/네트워크 오류이며, 두 경우 모두 실패로
취급합니다. 감사는 setup/verify/build에서만 registry에 접근하고 검색·읽기
경로에서는 사용하지 않으므로, 사내망에서는 npm registry 접근을 열어 준 뒤 다시
실행하세요. `--ignore-scripts`나 audit 생략으로 우회하지 않습니다.

### `audit-semantic.sh` 실패
`./scripts/audit-semantic.sh`는 `verify.sh`에서 model runtime lock
(`requirements/semantic.txt`)을 감사합니다. Kordoc 감사처럼 네트워크가 필요하며,
검토된 advisory 여섯 건만 이유와 함께 제외하고 새 advisory는 실패로 처리합니다
([SECURITY.md](SECURITY.md#dependency-safety)). 네트워크 오류면 접근을 열고 다시
실행하고, 새 advisory면 제외 목록에 추가하지 말고 검토를 요청하세요.

### `Semantic search: skipped` 또는 `the model runtime could not be installed`
Bootstrap이 semantic search용 model runtime을 준비하지 않았다는 뜻이며 설치 자체는
성공입니다. 검색은 lexical로 동작합니다. `KIP_SEMANTIC=off`로 실행했거나(`.env`에
기록됨) RAM이 8GiB 미만이면 건너뜁니다. 다운로드·디스크 문제로 실패했다면 원인을
해결한 뒤 다시 실행하세요.

```bash
./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch
```

Runtime 없이 새로 만든 `config/kip.toml`은 `semantic_enabled = false`입니다.
나중에 runtime을 설치했다면 `search.semantic_enabled = true`,
`search.default_mode = "hybrid"`, `[models.embedding] enabled = true`로 바꾸세요([OPERATIONS.md](OPERATIONS.md#semantic-search-default)).

### `docker: command not found` / `Cannot connect to the Docker daemon`
Docker Desktop이 설치되지 않았거나 실행 중이 아닙니다. Docker Desktop을 실행한
뒤(고래 아이콘이 "Running"이 될 때까지 기다린 후) 다시 시도하세요.

```bash
docker compose version   # 여기서 버전이 나와야 정상입니다
```

### `generated host config is mismatched; regenerate and apply a setup plan`
`./scripts/app-up.sh --database-only`가 `config/kip.host.generated.toml`의 plan
fingerprint 또는 database secret ref가 `config/kip.generated.toml`과 다른 것을
발견한 경우입니다. 두 파일을 손으로 맞추지 말고 setup으로 plan을 다시 생성해
승인·apply한 뒤 명령을 다시 실행하세요.

### `docker build`가 `resolve image config for docker-image://docker.io/docker/dockerfile:1.18@sha256:…`에서 멈춤
Dockerfile 첫 줄의 digest 고정 frontend를 BuildKit이 registry에 digest로
조회하는 단계입니다. Docker Desktop 내장 proxy(`http.docker.internal:3128`)
환경에서 이 HEAD 요청만 응답 없이 멈추는 사례가 있었습니다. 같은 digest의
이미지를 태그로 한 번 받아 두면 이후 빌드가 진행됩니다. digest가 Dockerfile의
값과 같은지 확인한 뒤 다시 빌드하세요. 고정된 digest를 지우거나 바꾸지 않습니다.

```bash
docker pull docker/dockerfile:1.18
docker image inspect docker/dockerfile:1.18 --format '{{index .RepoDigests 0}}'
# Dockerfile의 # syntax= 줄과 같은 sha256이어야 합니다
```

### `dependency_unavailable: PostgreSQL is not reachable at …`
데이터베이스가 떠 있지 않거나 `KIP_DATABASE_URL`이 다른 곳을 가리킵니다. 몇 초
안에 이 오류로 끝나며 접속 재시도 로그는 출력하지 않습니다.

```bash
./scripts/app-up.sh --database-only   # 번들 DB를 띄우고 migration까지 수행
./scripts/kip doctor                  # canonical_repository 항목으로 재확인
```

### `port is already allocated` / `address already in use` (5432)
이미 다른 PostgreSQL이 5432 포트를 쓰고 있습니다. 기존 것을 끄거나, `.env`에서
포트를 바꾸세요.

```bash
echo "KIP_POSTGRES_PORT=5433" >> .env
./scripts/app-up.sh --database-only
```

### Windows에서 스크립트가 실행되지 않음
PowerShell/cmd에서는 동작하지 않습니다. WSL2(Ubuntu)를 설치하고 그 안에서
실행하세요: 관리자 PowerShell에서 `wsl --install` 실행 후 재부팅.

### 디스크 공간 부족
최소 10GB가 필요합니다(`df -h .`로 확인). 런타임 이미지 약 2GB, OCR 모델 약
0.8GB, Python 환경 약 1GB, 나머지는 데이터베이스와 색인입니다. 기본 semantic
search를 쓰면 embedding model snapshot 약 1.2GB(`var/model-cache`, reranker를
켜면(`KIP_SEMANTIC_RERANKER=on`) 약 2.3GB 추가)와 격리 model runtime
(`var/semantic-venv`)이 추가됩니다. 공간이 부족하면 `KIP_SEMANTIC=off`로 lexical
전용 설치를 선택할 수 있습니다.

---

## 2. 검색 결과가 비어 있을 때

순서대로 확인하세요.

1. **색인이 되어 있는가**
   ```bash
   ./scripts/kip status    # data.content_units 가 0이면 아직 색인 전입니다
   ```
   0이면 먼저 수집을 실행하세요: `./scripts/kip sync run --source 소스이름`
   (쓸 수 있는 소스 이름은 `./scripts/kip doctor` 출력의
   `filesystem_source:이름` 점검 항목에서 확인할 수 있습니다).

2. **그 단어가 실제로 색인에 있는가**
   ```bash
   ./scripts/kip vocab "참여율" --limit 20
   ```
   아무것도 안 나오면 그 단어가 문서에 없거나 파일이 제대로 읽히지 않은 것입니다.

3. **파일이 읽혔는지 확인**
   `sync run` 출력의 `failed`와 `warnings`를 보세요. 특정 파일이 실패했다면
   경고 메시지에 파일명과 이유가 있습니다(아래 3장 참고).

4. **권한(ACL) 때문에 안 보이는가**
   내 권한 범위 밖의 자료는 존재 자체가 보이지 않습니다. 설정한
   `acl_scope`와 실행 시 사용하는 스코프가 같은지 확인하세요.

5. **허용 폴더 설정이 바뀌었는가**
   삭제·비활성화·root/분류/ACL 변경 후에는 이전 색인이 존재해도 숨겨질 수
   있습니다. 실제 선택된 config와 서비스 재시작 여부를 확인하세요. 의도한
   범위를 승인한 뒤 그 source만 명시적으로 sync합니다. 알려진 ID나 넓은
   request ACL로 이 경계를 우회할 수 없습니다.

### 결과에 `semantic_degraded` 또는 `rerank_degraded` 경고가 붙을 때

검색·context envelope의 `meta.warnings`에 나오는 경고이며 검색은 실패하지
않습니다.

- `semantic_degraded`: model runtime이 응답하지 않거나 semantic projection이 아직
  완성·활성화되지 않아 lexical 결과를 돌려줬습니다. `./scripts/kip doctor`의
  `semantic_search` 항목 `reason`이 할 일을 알려줍니다. Runtime이 멈췄다면
  `./scripts/semantic-server.sh start`, 설치되지 않았다면
  `./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch`,
  projection이 `missing`/`shadow`/`stale`이면
  `./scripts/kip sync run --source 소스이름` 또는
  `./scripts/kip projection rebuild --name semantic`을 실행합니다. 큰 corpus의
  첫 projection은 몇 시간이 걸리며, 그동안 이 경고가 계속 붙는 것은 정상입니다.
  Sync 중 runtime이 멈춰 있으면 `semantic_projection.status`가 `unavailable`이
  되고 다음 sync가 이어서 처리합니다.
  Runtime이 떠 있는데도 이 경고가 붙는다면 설정과 다른 model을 serve하고 있을 수
  있습니다. Infinity는 serve하지 않는 model 이름으로 요청해도 HTTP 200으로
  답하므로, KIP가 `GET /models`로 `models.embedding.model`(HTTP reranker면
  `models.reranker.model`)이 실제로 올라와 있는지 먼저 확인하고 다르면 runtime을
  사용하지 않습니다. `KIP_EMBEDDING_SERVED_MODEL`(reranker면
  `KIP_RERANKER_SERVED_MODEL`)이 다른 이름을 가리키거나, runtime을 다시 시작하지
  않고 `models.embedding.model`만 바꿨거나, 오래된 launchd/systemd unit이 남아
  있을 때 나타나며, `doctor`의 `reason`이 serve 중인 model 목록과 설정값을 함께
  보여줍니다. 설정한 model로 runtime을 다시 시작하거나 `models.embedding.model`을
  맞추세요.
  확인하는 것은 serve 이름뿐입니다. Infinity 0.0.77의 `GET /models`는 revision도
  weight hash도 알려주지 않으므로 **같은 이름으로 다른 weight나 다른 revision을
  serve하면 KIP는 알아채지 못하고**, `models.embedding.revision`은 runtime에서
  검증되지 않습니다. 그래서 `KIP_EMBEDDING_MODEL`/`KIP_EMBEDDING_REVISION`(그리고
  `KIP_SEMANTIC_RERANKER=on`일 때 `KIP_RERANKER_MODEL`/`KIP_RERANKER_REVISION`)으로
  기본값과 다른 model을 띄우려 하면 `./scripts/semantic-server.sh`가 시작을
  거부합니다. 후보 model을 평가할 때는 `KIP_EMBEDDING_SERVED_MODEL`로 다른 이름을
  주고 `models.embedding.model`도 그 이름으로 맞추세요.
- `rerank_degraded`: 기본 mode를 `reranked`로 바꾼 배포에서 reranker만 실패해
  lexical+vector fusion 순위를 그대로 돌려줬습니다. BGE reranker
  (`models.reranker.backend = "http"`)는 runtime을 `KIP_SEMANTIC_RERANKER=on`으로
  시작해야 load됩니다. Runtime 로그(`start`로 띄웠다면 `var/log/semantic-server.log`, launchd면
`var/log/launchd-semantic.err.log`)를 확인하세요.

Runtime이 한 번 실패하면 `models.circuit_cooldown_seconds`(기본 30초) 동안 호출을
건너뛰므로 복구 직후 잠시 경고가 남을 수 있습니다. Runtime이 메모리를 많이 써서
시스템이 느려진다면 `max_document_chars`가 기본값 4000인지 확인하세요. 12000을
유지한 이전 config는 24GB Mac에서 runtime을 15GB까지 키웠고, 검토되지 않은
embedding identity라 자동 활성화도 되지 않습니다. 기준 Apple Silicon(24GB,
float16)에서 embedding model만 load한 runtime은 약 2.7GB를 씁니다. Reranker를
켜면 약 2GB가 더 필요합니다. `./scripts/semantic-server.sh stop`은 최대 20초
(`KIP_SEMANTIC_STOP_SECONDS`) 기다린 뒤 강제 종료하므로 바쁜 runtime이 남지
않습니다. `start`는 launchd/systemd나 다른 instance가 이미 실행 중이거나 응답하면
새로 띄우지 않습니다.

`kip capabilities`의 `semantic_projection_status`는 active space가 설정된
identity와 일치하는지만 빠르게 봅니다(`active`/`incompatible`/`shadow`/`missing`).
빠진 unit이 있는지(`stale`)는 `./scripts/kip doctor`의 `semantic_search` 항목이나
`./scripts/kip projection verify --name semantic`으로 확인하고,
`./scripts/kip projection rebuild --name semantic`으로 채웁니다.

전체 `./scripts/app-up.sh`는 machine당 model runtime을 하나만 띄웁니다. ARM
machine이나 host runtime이 이미 떠 있는 경우 API/worker container의 검색 결과에
`semantic_degraded`가 붙는 것은 정상입니다(host CLI/MCP는 host runtime을
씁니다).

---

## 3. 특정 파일이 색인되지 않을 때

`sync run` 결과의 `warnings`에는 오류와 원인별 skip 집계가 나옵니다. 자주 나오는 경우:

| 메시지에 나오는 말 | 뜻과 해결 |
|---|---|
| `no parser registered for .xxx` | 지원하지 않는 확장자입니다. 설정의 `include_extensions`를 확인하세요. |
| `PDF parse failed` / `DOCX parse failed` | 파일이 손상되었거나 암호가 걸려 있습니다. 원본을 열어보세요. |
| `ENCODING_UNCERTAIN` | 글자 인코딩을 자동 판별하지 못했습니다(대개 오래된 CSV/TXT). 파일을 UTF-8로 다시 저장하면 해결됩니다. |
| `OCR_FAILED` | 스캔 이미지 문자 인식 도구(Kordoc)를 찾지 못했습니다. 아래 4장 참고. |
| `parser process timed out` | 파일 하나가 `[parsers.isolation].wall_seconds`를 넘었습니다. 원본은 바뀌지 않고 이전 active extraction이 유지됩니다. 같은 파일을 읽기 전용으로 재현해 시간/RSS를 측정한 뒤에만 한도를 조정하세요. |
| `parser process exceeded memory budget` | child와 descendants의 합산 RSS가 `memory_mib`를 넘었습니다. 동시 실행을 늘리지 말고 파일 크기·형식·peak RSS를 기록한 뒤 `OPERATIONS.md`의 headroom 규칙으로 조정하세요. |
| `parser process response exceeded` / `invalid response` | 결과 파일이 `result_mib`를 넘었거나 child contract가 손상됐습니다. 한도를 무작정 풀지 말고 해당 parser/version과 unit 수를 격리 표본으로 재현하세요. |
| `cloud` / `placeholder` 집계 | OneDrive 등에서 파일 내용이 로컬에 없습니다. provider 앱에서 원하는 표본만 다운로드하고 preview를 다시 실행하세요. KIP은 placeholder를 다운로드하지 않고 이전 extraction도 삭제하지 않습니다. |
| filter / size / symlink / settle 집계 | 안정화 대기 또는 수집 정책에 걸린 파일입니다. 이전 active extraction은 유지하고 삭제로 처리하지 않습니다. |
| `filesystem scan incomplete` | 하위 디렉터리를 읽지 못해 삭제 조정을 중단했습니다. NAS mount와 디렉터리 권한을 복구한 뒤 다시 sync하세요. |
| `partial` 상태 + 낮은 quality | 일부만 추출되었습니다. 원본 확인 후 필요하면 다시 저장해서 재수집하세요. |

파서를 개선한 뒤 기존 파일에도 반영하려면 재수집이 필요합니다
(`./scripts/kip sync run --source 이름` 재실행).

Reference 설정에서는 모든 filesystem parser가 파일 하나당 fresh child에서
실행됩니다. 개발 비교가 아니라면 `[parsers.isolation].enabled = false`로 우회하지
마세요. Timeout이나 memory failure 뒤 `ps`에
`kip.adapters.parsers.isolated_worker`가 남거나 임시 디렉터리가 정리되지 않으면
운영 결함으로 보고해야 합니다.

---

## 4. `kip doctor`가 경고할 때

| 검사 이름 | 뜻과 해결 |
|---|---|
| `configuration` | 설정 파일을 찾지 못했습니다. `KIP_CONFIG` 환경변수나 `config/kip.toml` 존재를 확인하세요. |
| `canonical_repository` | 데이터베이스에 연결하지 못했습니다. `./scripts/app-up.sh --database-only`로 PostgreSQL이 떠 있는지, `KIP_DATABASE_URL`이 맞는지 확인하세요. |
| `content_addressed_store` | 원본 사본 저장 폴더(CAS)에 접근할 수 없습니다. 경로 권한을 확인하세요. |
| `filesystem_source:이름` | 그 소스 폴더가 없거나 읽을 수 없습니다. 경로와 접근 권한을 확인하세요. |
| `semantic_search` | Semantic search가 켜져 있는데 model runtime에 연결할 수 없거나, runtime이 설정과 다른 model을 serve하고 있거나, projection이 active가 아니거나 완성되지 않았습니다(`stale`). 필수 항목은 아니며 그동안 검색은 lexical로 동작합니다. 다른 model을 serve하는 경우 `details.reason`이 serve 중인 model 목록과 `models.embedding.model`(HTTP reranker면 `models.reranker.model`)을 함께 보여주며, 그 model로 runtime을 다시 시작하면 됩니다. 나머지는 `details.reason`의 명령(`./scripts/semantic-server.sh start`, `./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch`, `./scripts/kip sync run --source 소스이름` 또는 `./scripts/kip projection rebuild --name semantic`)을 실행하세요. |
| `kordoc_ocr_resolvable` | OCR이 켜져 있는데 `kordoc` 실행 파일을 찾지 못했습니다. `./scripts/install-kordoc.sh`를 실행하거나, 스캔 문서가 없다면 설정에서 `parsers.ocr.kordoc.enabled = false`로 끄세요. 끄지 않으면 이미지가 든 PDF/PPTX가 `partial`로 처리됩니다. |
| `ontology_adaptive_discovery_writable` | 새 용어 제안 기능이 켜져 있는데 `ontology/` 폴더에 쓸 수 없습니다. 컨테이너라면 그 폴더가 쓰기 가능하게 연결(마운트)되어야 합니다. |
| `ontology_pending_release_journal` | 이전 작업이 중단된 흔적이 남아 있습니다. 다음 실행 때 자동 복구되며, 계속 남아 있으면 파일 권한을 확인하세요. |

---

## 5. 명령이 거부될 때 (오류 코드별)

모든 명령은 같은 형태의 JSON을 돌려줍니다. `error.code`를 보세요.

| `error.code` | 뜻 | 해결 |
|---|---|---|
| `validation_error` | 입력값이 잘못됨 | 메시지에 어떤 항목이 문제인지 나옵니다. 빈 검색어·잘못된 범위 등. |
| `not_found` | 해당 ID가 없음 | ID 오타이거나, 내 권한으로는 보이지 않는 자료입니다. |
| `forbidden` | 권한 부족 | 온톨로지 승인·철회·채굴 등은 **관리자 역할**이 필요합니다. `--role admin`을 붙이거나 `KIP_ROLES=admin`을 설정하세요. |
| `conflict` | 이미 처리됨/충돌 | 같은 작업이 이미 반영되었거나 원본이 중간에 바뀐 경우입니다. 다시 조회 후 재시도하세요. |
| `dependency_unavailable` | 외부 구성요소 없음 | 임베딩 서버나 선택 기능이 꺼져 있습니다. `--mode`로 `vector`, `hybrid`, `reranked`를 명시한 요청은 lexical로 대체되지 않고 실패하므로, runtime을 켜거나(`./scripts/semantic-server.sh start`) mode를 생략해 기본 fallback을 쓰세요. |
| `configuration_error` | 설정값 오류 | 메시지가 어떤 설정 키가 잘못됐는지 알려줍니다. |
| `parser_error` | 파일을 읽지 못함 | 3장 표를 보세요. |

### 자주 겪는 경우: `forbidden`
온톨로지 후보 승인/거절, 사실 철회, 관계 채굴, 엔티티 생성은 관리자 전용입니다.

**주의: `--role admin`은 하위 명령보다 앞에 와야 합니다.**

```bash
# 올바른 형태 (--role 이 review 보다 앞)
./scripts/kip --role admin review approve CANDIDATE_ID --note "확인함"

# 틀린 형태 — "No such option: --role" 오류가 납니다
./scripts/kip review approve CANDIDATE_ID --role admin

# 매번 붙이기 번거로우면 환경변수로 설정
export KIP_ROLES=admin
./scripts/kip review approve CANDIDATE_ID --note "확인함"
```

---

## 6. 엑셀 숫자 질문에 답을 거부할 때

`refused: true`와 `exact_xlsx_read_required`(또는 CSV의
`csv_full_table_required`)가 나오면 정상 동작입니다. 얕은 색인의 텍스트로
합계를 지어내지 않고, 원본 셀 범위를 직접 읽으라는 뜻입니다.

```bash
./scripts/kip xlsx-read ARTIFACT_ID --sheet "정산" --range "A1:F40"
```

---

## 7. 그래도 해결되지 않으면

1. `./scripts/kip doctor`와 실패한 명령의 전체 JSON 출력을 저장하세요.
2. `./scripts/kip capabilities`와 `./scripts/kip status` 출력도 함께 모으세요.
3. 위 세 가지와 "무엇을 하려고 했는지"를 담당자나 AI 에이전트에게 전달하면
   원인을 빠르게 좁힐 수 있습니다. 비밀번호·API 키는 절대 포함하지 마세요.
