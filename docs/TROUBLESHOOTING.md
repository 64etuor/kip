# 문제 해결 (Troubleshooting)

막혔을 때 여기서 증상을 찾아 순서대로 따라 하세요. 용어가 낯설면
[`GLOSSARY.md`](GLOSSARY.md)를 먼저 보세요.

**어떤 명령이든 실패하면 가장 먼저 이 두 개를 실행하세요.**

```bash
./scripts/kip doctor    # 설정·저장소·폴더·OCR 상태를 점검하고 해야 할 일을 알려줍니다
./scripts/kip status    # 실제로 몇 건이 색인되어 있는지 보여줍니다
```

`doctor` 출력에서 `"ok": false`인 항목의 `reason`이 다음에 할 일입니다.

---

## 1. 설치가 안 될 때

### `./scripts/bootstrap.sh`가 Python 버전 오류로 멈춤
Python 3.12 이상이 필요합니다.

```bash
python3 --version
# 3.12 미만이면 새로 설치한 뒤
rm -rf .venv && ./scripts/bootstrap.sh
```

### `docker: command not found` / `Cannot connect to the Docker daemon`
Docker Desktop이 설치되지 않았거나 실행 중이 아닙니다. Docker Desktop을 실행한
뒤(고래 아이콘이 "Running"이 될 때까지 기다린 후) 다시 시도하세요.

```bash
docker compose version   # 여기서 버전이 나와야 정상입니다
```

### `port is already allocated` / `address already in use` (5432)
이미 다른 PostgreSQL이 5432 포트를 쓰고 있습니다. 기존 것을 끄거나, `.env`에서
포트를 바꾸세요.

```bash
echo "KIP_POSTGRES_PORT=5433" >> .env
./scripts/dev-up.sh
```

### Windows에서 스크립트가 실행되지 않음
PowerShell/cmd에서는 동작하지 않습니다. WSL2(Ubuntu)를 설치하고 그 안에서
실행하세요: 관리자 PowerShell에서 `wsl --install` 실행 후 재부팅.

### 디스크 공간 부족
최소 10GB가 필요합니다(`df -h .`로 확인). 런타임 이미지 약 2GB, OCR 모델 약
0.8GB, Python 환경 약 1GB, 나머지는 데이터베이스와 색인입니다.

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

---

## 3. 특정 파일이 색인되지 않을 때

`sync run` 결과의 `warnings`에 파일별 이유가 나옵니다. 자주 나오는 경우:

| 메시지에 나오는 말 | 뜻과 해결 |
|---|---|
| `no parser registered for .xxx` | 지원하지 않는 확장자입니다. 설정의 `include_extensions`를 확인하세요. |
| `PDF parse failed` / `DOCX parse failed` | 파일이 손상되었거나 암호가 걸려 있습니다. 원본을 열어보세요. |
| `ENCODING_UNCERTAIN` | 글자 인코딩을 자동 판별하지 못했습니다(대개 오래된 CSV/TXT). 파일을 UTF-8로 다시 저장하면 해결됩니다. |
| `OCR_FAILED` | 스캔 이미지 문자 인식 도구(Kordoc)를 찾지 못했습니다. 아래 4장 참고. |
| `parser process timed out` | 파일 하나가 `[parsers.isolation].wall_seconds`를 넘었습니다. 원본은 바뀌지 않고 이전 active extraction이 유지됩니다. 같은 파일을 읽기 전용으로 재현해 시간/RSS를 측정한 뒤에만 한도를 조정하세요. |
| `parser process exceeded memory budget` | child와 descendants의 합산 RSS가 `memory_mib`를 넘었습니다. 동시 실행을 늘리지 말고 파일 크기·형식·peak RSS를 기록한 뒤 `OPERATIONS.md`의 headroom 규칙으로 조정하세요. |
| `parser process response exceeded` / `invalid response` | 결과 파일이 `result_mib`를 넘었거나 child contract가 손상됐습니다. 한도를 무작정 풀지 말고 해당 parser/version과 unit 수를 격리 표본으로 재현하세요. |
| `present but skipped from ingestion (filter, size, symlink, or settle policy)` | 파일이 아직 안정화 대기 중이거나 필터·용량·symlink 정책에 걸렸습니다. 원본과 이전 active extraction은 그대로 있고 삭제로 처리되지 않습니다. |
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
| `canonical_repository` | 데이터베이스에 연결하지 못했습니다. `./scripts/dev-up.sh`로 PostgreSQL이 떠 있는지, `KIP_DATABASE_URL`이 맞는지 확인하세요. |
| `content_addressed_store` | 원본 사본 저장 폴더(CAS)에 접근할 수 없습니다. 경로 권한을 확인하세요. |
| `filesystem_source:이름` | 그 소스 폴더가 없거나 읽을 수 없습니다. 경로와 접근 권한을 확인하세요. |
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
| `dependency_unavailable` | 외부 구성요소 없음 | 임베딩 서버나 선택 기능이 꺼져 있습니다. 그 기능을 켜거나 다른 검색 모드를 쓰세요. |
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
