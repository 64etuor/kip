# Operations

> 용어가 낯설면 [`GLOSSARY.md`](GLOSSARY.md), 문제가 생기면
> [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)를 보세요.

## 자주 하는 작업 (Everyday tasks)

설치 전에는 `./scripts/bootstrap.sh --check`로 준비 상태를 확인한다.
기본 bootstrap은 없는 Python/Node를 프로젝트 안에 준비하며
`--install-docker`는 시스템 Docker 설치를 허용한다. OS 암호·Desktop
약관과 첫 실행 선택은 사용자가 처리해야 한다. 완료되지 않은 경우 같은
명령을 재실행할 수 있으며 기존 Docker context/그룹 권한은 바꾸지 않는다.
외부 DB만 쓰는 CLI/MCP 설치는 `--without-docker`를 지정한다. 다운로드는
pin과 checksum을 확인하며 실제 엔진 접근까지 성공해야 Docker ready다.

CLI/MCP만 쓰는 배포는 `./scripts/app-up.sh --database-only`부터 실행한다.
generated 배포에서는 승인된 `postgres` 서비스만 기동해 준비를 기다린 뒤
`config/kip.host.generated.toml`의 plan fingerprint와 database secret ref가
generated config와 일치하는지 확인하고 host `./scripts/migrate.sh`를 실행한다.
DB credential만 필요하며 API/worker/identity credential은 읽지도 요구하지도
않는다(Compose 보간용 placeholder만 주입). external DB(번들 postgres 서비스가
없는 plan)는 Docker를 띄우지 않고 migration만 수행한다. generated 배포가 아니면
`compose.yaml`의 `postgres`만 올린 뒤 migration한다. 이 경로는 API/worker 이미지를
빌드하지 않는다. 인자는 하나만 받는다. Host model runtime(`var/semantic-venv`)이
설치돼 있고 `KIP_SEMANTIC=off`가 아니면 `semantic-server.sh start`와 `wait`로
runtime을 띄우고 준비를 기다린다. 실패해도 경고만 남기고 검색은 lexical로
동작한다([Semantic search](#semantic-search-default)).

REST API나 worker(예약 sync, parser worker)가 필요할 때만 전체
`./scripts/app-up.sh`를 실행한다. standalone generated Compose가 DB 준비와
migration을 순서대로 수행하며 승인된 source mount만 연결한다. 중지는
`./scripts/app-up.sh --down`이다. 전체 `app-up.sh`는 machine당 model runtime을
하나만 띄운다. amd64에서는 compose `models` service(profile `semantic`)를 켜고
`127.0.0.1:${KIP_SEMANTIC_PORT:-7997}`에 publish해 host CLI/MCP도 쓰게 한다.
ARM이거나 host runtime이 이미 응답하면 host runtime을 쓰고, 이때 API/worker
container는 lexical 검색(`semantic_degraded`)으로 동작한다. RAM이 8 GiB 미만이거나
`KIP_SEMANTIC=off`면 runtime을 띄우지 않는다. wrapper는 generated host config를 기본
선택하고, 명시적 config나 exported override는 유지한다. 기본 `env:KIP_DATABASE_URL`은 bundled local DB와
일치해야 하며 external DB는 별도 변수의 secret reference를 선택한다. 모든
서비스와 host CLI가 같은 DB를 사용하는지 receipt/readiness로 확인한다.

Guided source는 host의 canonical 절대경로 그대로 container에 mount한다.
두 config의 source root가 같아야 공유 DB의 URI/ACL snapshot도 일치한다.
이전 split-path plan은 재생성·apply 후 명시적으로 sync한다. runtime 보호
경로, 중복 mount target, model credential/source 겹침은 plan에서 거부된다.
`./scripts/curl-smoke.sh`는 승인한 config의 API port/key로 `/readyz`와
capabilities를 확인한다. 임의 identity header를 넣지 않는다.

**허용 폴더 변경하기.** setup으로 만든 배포는 source 질문을 다시 답하고 새
plan을 승인·apply한다. 수동 배포는 선택된 config의 `sources.filesystem`을
수정한다. API/worker/MCP를 재시작하여 정책을 로드하면 제거·비활성화·변경된
root의 이전 색인도 차단된다. 남긴 범위는 명시적 sync로 다시 확인한다.
색인 삭제나 자동 rebuild 없이 접근 경계를 바꾸는 절차다 (ADR-056).

**문서 폴더 하나 더 추가하기.** 수동 배포의 선택된 설정 파일에 블록을 하나
추가하고 그 소스만 수집하면 됩니다. 원본 폴더는 읽기 전용으로만 연결되며
수정되지 않습니다.

```toml
[[sources.filesystem]]
name = "sales-team"                 # 영문 소문자 별명 (수집할 때 쓰는 이름)
root = "/mnt/nas/영업팀"             # 실제 절대경로
enabled = true
read_only = true
include_extensions = [".md", ".txt", ".pdf", ".hwp", ".hwpx", ".docx", ".xlsx", ".pptx", ".csv"]
exclude_globs = ["**/.DS_Store", "**/~$*", "**/backup/**"]
acl_scope = "workspace:default"     # 이 자료를 볼 수 있는 그룹 이름표
classification = "internal"          # public / internal / confidential / restricted / personal
```

```bash
# doctor 의 checks 에 filesystem_source:sales-team 이 ok:true 로 보이면 등록된 것
./scripts/kip doctor
./scripts/kip sync run --source sales-team     # 그 폴더만 수집
./scripts/kip status                           # content_units 가 늘었는지 확인
```

**바뀐 파일 다시 반영하기.** 같은 명령을 다시 실행하면 됩니다. 변경된 파일만
다시 읽고, 그대로인 파일은 건너뜁니다. 검색은 절대 수집을 자동으로 시작하지
않습니다.

**문서가 검색되지 않을 때.** `./scripts/kip status`로 색인 건수를 보고,
`./scripts/kip vocab "단어"`로 그 단어가 색인에 있는지 확인한 뒤,
`sync run` 출력의 `failed`/`warnings`에서 파일별 이유를 확인하세요. 자세한
절차는 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) 2·3장에 있습니다.

**후보 검토가 필요한지 확인.** `./scripts/kip ontology candidates --status
proposed`가 비어 있으면 할 일이 없습니다. 관계 채굴을 켜지 않은 일반 설치에서는
계속 비어 있는 것이 정상이며, 온톨로지 명령을 배우지 않아도 검색·답변은 그대로
동작합니다.

### 업그레이드

패키지로 설치한 배포는 `./scripts/upgrade.sh`로 제자리에서 올린다. 같은
디렉터리에 릴리스 설치기를 다시 실행해도 이 스크립트가 실행된다.

```bash
./scripts/upgrade.sh --latest --dry-run                          # 계획과 CHANGELOG만
./scripts/backup.sh
./scripts/upgrade.sh --latest    # 또는 --version X.Y.Z / --archive ZIP
```

한 줄 설치기로 만든 배포에는 전역 `kip` 명령이 있다. `kip update`는
`./scripts/upgrade.sh --latest`와 같고 `--version X.Y.Z`, `--archive ZIP`,
`--dry-run`, `--rollback`(특정 기록은 `--rollback-id ID`), `--no-bootstrap`을
그대로 받으며 스크립트 출력을 그대로 흘려보낸다. `--rollback`은 미리보기가
없어 `--dry-run`·`--archive`·`--version`과 함께 주면 실행 전에 거부된다. `kip update`와 `kip version`은 데이터베이스 없이도 실행되고,
git 체크아웃은 `git pull`을 쓰라며 거부된다. `--latest`/`--version`은 설치기를
`--keep-launcher`로 부르므로 런처가 이미 이 배포를 가리킬 때만 런처를 새로 쓰고
셸 프로필은 바꾸지 않는다(3.12.1). 다른 배포를 가리키는 `kip`, `--bin-dir`로 둔
런처, `--no-shell-profile`로 설치한 배포는 업데이트 후에도 그대로다. 단
3.10.0-3.12.0 배포의 다음 업데이트는 그 배포의 이전 설치기가 실행해 기본 런처와
프로필 블록을 한 번 다시 쓴다(`docs/DEPLOYMENT_GUIDE.md` 설치 절 참고).

설치된 manifest나 새 manifest에 있는 패키지 소유 파일만 교체·삭제되고 `.env`,
`config/kip*.toml`, `compose.generated.yaml`, `var/`, `secrets/`, ontology·golden
추가분은 건드리지 않으며 `.mcp.json`은 보존된다. 교체·삭제된 파일과 `plan.json`은
`var/upgrades/<id>/`에 남고 `./scripts/upgrade.sh --rollback [ID]`가 그 패키지 파일을
되돌린다(설치된 버전이 해당 업그레이드의 대상 버전이 아니면 거부한다). 적용 뒤에는
`./scripts/bootstrap.sh`, `./scripts/migrate.sh`, `./scripts/kip doctor`가 이어지며,
데이터베이스에 연결할 수 없으면 `Action required`와 함께 exit 75로 끝난다. 이때는
`./scripts/app-up.sh --database-only`로 DB를 올린 뒤 `./scripts/migrate.sh`와
`./scripts/kip doctor`를 실행한다. 마이그레이션이 포함된 업그레이드 뒤에는
`./scripts/app-up.sh`(또는 `--database-only`)를 한 번 실행해 `roles` 서비스가
`deploy/sql/roles.sql.template`을 다시 적용하게 한다. grant는 실행 시점에 존재하는
테이블만 덮으므로, 재적용 전까지 새 테이블에는 `kip_api`/`kip_worker` 권한이 없다. `--dry-run`은 `--latest`, `--version`, `--archive`
모두에 적용되며 다운로드와 검증만 하고 파일은 바꾸지 않는다. 버전 하향, git 체크아웃(`git pull`로 갱신), digest나 manifest가 맞지 않는
아카이브는 거부된다. rollback은 패키지 파일만 되돌리고 데이터베이스는 복구하지
않으므로, 마이그레이션을 지나는 업그레이드 전에는 반드시 `./scripts/backup.sh`를
실행한다. 3.9.0 이전에 만든 배포에는 `scripts/upgrade.sh`가 없으므로
[`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) 11.5의 수동 절차를 한 번 거친 뒤
설치기를 쓴다.

`kip update`는 `config/kip*.toml`을 보존하므로 `semantic_enabled = false`인 기존
배포는 3.12.0 이후에도 lexical로 남는다. 기본 semantic search를 채택하는 절차는
[Semantic search](#semantic-search-default)의 "Adopting the default on an existing
deployment"에 있다.

3.10.0 이전에 설치한 배포도 같은 절차로 올라간다. 업그레이드는 레거시
`STARTER-KIT-MANIFEST.json`을 읽어 그대로 적용한 뒤 그 파일을 제거하고
`KIP-MANIFEST.json`으로 대체하며, `--rollback`은 레거시 매니페스트까지
복원한다. 릴리스는 `kip-<version>.zip` 외에 레거시 형식 사본
(`kip-starter-kit-<version>.zip`과 sidecar: 같은 내용, 매니페스트만 예전 이름과
schema 식별자)도 게시하므로 3.9.x의 업그레이더도 그대로 적용할 수 있다.
최신 설치기는 기존 배포를 올릴 때 내려받은 아카이브 안의 업그레이더를 쓰고
새 트리의 `upgrade.sh --finish`로 bootstrap·migrate·doctor를 이어간다. 또 3.10.0 이전 릴리스를 `--version`으로 고정하면 최신
설치기가 예전 자산 이름으로 되돌아가 받는다.

### Database readiness errors

`kip` commands that need PostgreSQL fail within seconds with
`dependency_unavailable` naming the host, port and database when the server
is unreachable; the message points at `./scripts/app-up.sh --database-only`
and `./scripts/kip doctor`. Driver reconnect warnings are suppressed unless a
deployment configures the `psycopg.pool` logger itself. Migrations 0026 and
0027 add the casefolded basename index and the lexical-unit artifact index
used by named-file answers; apply them with the usual `./scripts/migrate.sh`
(or `app-up.sh --database-only`).

### Named-document answers

For a named-document answer, use a literal quoted filename, for example:

```bash
./scripts/kip answer '"보고서.pdf" 제출기한은 언제인가?' --limit 5
```

As of 3.7.1, that file scope is fixed before ranking and live source checks.
Unknown/inaccessible or stale evidence returns a typed refusal; another file
is not substituted. If several files are named, all must yield usable evidence
within the requested limit. Inspect the source and narrow the request or raise
the limit as appropriate; ordinary retrieval still never starts a sync.

## Daily

`verify.sh` checks local Markdown links against the online package's document
payload. Both source and production bundles include canonical documents and
ADRs; historical designs/audits remain in Git history through pinned links.
Live model-behavior checks are recorded in `docs/AGENT_QUALITY.md` and are not
part of the deterministic CI gate.

`./scripts/bootstrap.sh` runs `scripts/prerequisites.sh` first: pure Bash, before
dotenv parsing. That stage reuses a compatible Python 3.12+, or downloads the
checksum-pinned uv recorded in `requirements/bootstrap.tsv` (version and
SHA-256; currently uv 0.12.12) into `var/runtime/uv-<version>` and installs a
managed Python 3.13.x under `var/runtime/python`. A stdlib-only Python stage
then prepares Node/npm from the pinned Node 22 bundle when missing and checks
Docker/Compose. Afterwards `.venv` is created with `uv venv` and synchronized
from `uv.lock` with `uv sync --frozen` and the postgres, api, identity,
extractors, mcp, and dev extras. Wrappers select the managed runtimes
through `scripts/runtime-path.sh` (`var/runtime/bin`), and `./scripts/uv.sh`
runs the resolved uv. `--check` is read-only; `--install-docker` and
`--without-docker` are documented in ADR-061. Existing `.env` and config files
are preserved; a fresh `.env` receives random credentials rather than sample
placeholders. Bootstrap does not perform an unbounded dependency upgrade.
Bootstrap then installs the isolated semantic model runtime and prefetches the
pinned embedding snapshot unless `KIP_SEMANTIC=off` or the host has less than
8 GiB of RAM; a failure there never fails bootstrap (see
[Semantic search](#semantic-search-default)).

For code or release validation, run `./scripts/verify.sh`. It preflights pytest,
Ruff, mypy, and pip-audit and fails with a bootstrap remediation if any tool is
missing. With uv it uses the frozen lock; without uv it runs modules from the
project interpreter, including tools installed by `./scripts/bootstrap.sh`.
Only a completed gate is verification evidence. Private golden-set skips remain
explicit and do not prove private-corpus acceptance. The private gate skips when
the reviewed dataset or floor is absent, the repository is in-memory, the
workspace is empty, or none of the reviewed set's expected documents are indexed
there (for example a workspace holding only `sample-data`); with
`KIP_REQUIRE_PRIVATE_GOLDEN=1` each of those conditions fails instead.

Portable skill installation is `./scripts/install-agent-files.sh personal` or
`./scripts/install-agent-files.sh project /path/to/project`. It replaces only
the two bundled skills under `.claude/skills`, stages both before replacement,
and rolls back handled failures. Other skill directories remain untouched.
Symlink bundles are rejected. A machine interruption can leave
`.claude/skills/.kip-install-*` containing previous bundles; preserve those
until the installation is reconciled. The runtime pointer under
`~/.config/kip/project-root` changes only after both replacements succeed.
`KIP_PROJECT_DIR` overrides discovery and an invalid value is an error.

```bash
./scripts/doctor.sh
./scripts/kip status
./scripts/kip jobs list --status failed
./scripts/ops-report.sh
```

`ops-report.sh` is the single-command health summary: failed-job count,
oldest queued-job age, last successful sync progress, disk free on the volume
holding `var/`, newest sealed backup age, and API health on the loopback
port. It exits nonzero with an `OPS-REPORT FAIL:` summary line when a check
breaches its threshold, supports `--json` for machine consumption, and POSTs
the JSON report to `KIP_OPS_WEBHOOK` when set and failing. Thresholds are
tunable through `KIP_OPS_MAX_QUEUE_AGE_SECONDS` (default 3600),
`KIP_OPS_MAX_SYNC_AGE_SECONDS` (unset means warn only),
`KIP_OPS_DISK_MIN_FREE_PCT` (default 10), and
`KIP_OPS_BACKUP_MAX_AGE_HOURS` (default 26). An API that is not running is a
warning, not a failure; an API that answers non-200 is a failure. The API
check probes `/readyz` (which round-trips the database) and falls back to the
liveness-only `/healthz` when a deployment predates `/readyz` and answers 404.

## Incremental sync

Use source-specific cursors. A source outage must not be interpreted as deletion.

### Filesystem deletion grace policy

Filesystem deletion is reconciled by consecutive complete scans, controlled by
`[sync] deletion_grace_scans` (default `2`, minimum `1`):

- Only a COMPLETE, successful scan contributes absence evidence. A scan that
  fails, encounters a directory walk error, or aborts mid-walk changes nothing,
  and a scan that sees zero files is treated as a possible mount outage: it
  skips deletion reconciliation entirely and records a warning.
- After each complete scan, every active indexed object the scan did not see
  gets its consecutive-absence counter incremented (reported as `absent` in
  the sync summary). A file that reappears — or that failed to parse but was
  still seen on disk — clears its absence mark and restarts the count.
- Once an object stays absent for `deletion_grace_scans` consecutive complete
  scans, it is soft-deleted with the same tombstone-revision semantics used by
  event connectors (reported as `tombstoned`): a tombstone revision with zero
  content units becomes the current revision, the object leaves search,
  context bundles, and ontology evidence, and prior revisions, extraction
  history, and approved assertions are preserved. Nothing is ever hard-deleted
  and the source tree is never written.
- A file that reappears after tombstoning is re-indexed by the next sync and
  becomes searchable again.
- Dry-run syncs never mark absences and never tombstone. Files still present
  but deferred by the settle window, symlink policy, extension/exclusion
  filters, or the size limit count as seen and retain any prior active
  extraction. Scope changes therefore stop future ingestion without silently
  turning a present source into a deletion event.

## Production deployment

`compose.yaml` is the local pilot profile. Production starts from
`compose.production.yaml` and an operator-owned copy of
`deploy/production.env.example`:

```bash
install -m 600 deploy/production.env.example /etc/kip/production.env
# Replace every example URL, path, and zero digest in /etc/kip/production.env.
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml config
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml up -d postgres
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml --profile migration run --rm migrate
# Creates kip_api / kip_worker / kip_reviewer / kip_backup and their grants as
# the owner, after the migration that created the tables. Re-run it after every
# later migration.
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml --profile migration run --rm roles
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml --profile models-fetch run --rm models-fetch
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml up -d models api worker
```

The `models` service (ADR-065) runs the digest-pinned
`michaelf34/infinity:0.0.77-cpu` image (linux/amd64, CPU; override with
`KIP_MODELS_IMAGE`) read-only and offline (`HF_HUB_OFFLINE=1`) on the internal
`models` network, serving the pinned embedding model on port 7997. Its command
is embedding-only; to use the BGE reranker in containers, append
`--model-id BAAI/bge-reranker-v2-m3 --revision
953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e --served-model-name
kip-bge-reranker-v2-m3 --batch-size 2 --dtype float32` to the service command
and uncomment the `backend = "http"`, `base_url = "http://models:7997"`,
`model`, and `revision` lines under `[models.reranker]` in
`config/kip.container.toml` (the lexical path keeps BM25 through
`[models.lexical_reranker]`). `models-fetch` is the
only service with the `model-egress` network; run it once to populate the
`kip_models` volume with both models, so enabling the reranker later works
offline. In `compose.yaml` the service is in the `semantic` profile, which
`app-up.sh` enables on amd64 hosts unless a host runtime of the checkout runs
(even while it is still loading) or a runtime other than `models` answers on
the port; it keeps the profile while `models` runs so an upgrade recreates it.
API and worker reach
`http://models:7997` because `security.model_service_hosts = ["models"]`
treats that host as local while `allow_remote_model_egress` stays false. They
do not depend on the service: search degrades to lexical while it starts.

Both compose files pass PostgreSQL memory settings:
`shared_buffers=${KIP_POSTGRES_SHARED_BUFFERS:-1GB}`,
`effective_cache_size=${KIP_POSTGRES_EFFECTIVE_CACHE_SIZE:-3GB}`,
`work_mem=${KIP_POSTGRES_WORK_MEM:-16MB}`, and
`maintenance_work_mem=${KIP_POSTGRES_MAINTENANCE_WORK_MEM:-512MB}`. The 128MB
default made lexical queries and HNSW inserts thrash on a corpus of about 180k
units. An existing container picks these up only when recreated
(`docker compose up -d postgres`).

All three KIP image variables must use the same verified
`repository@sha256:<64 lowercase hex>` reference. Secret paths must be absolute
paths to regular, non-symlink, single-line files with operator-only
permissions. The PostgreSQL password and migration URL use owner credentials;
the API and worker URLs must use separate login roles bound to the NOLOGIN
groups in `deploy/sql/roles.sql.template`. Compose applies that template for
you: the `roles` service in the `migration` profile runs
`deploy/apply-roles.sh` with the migration (owner) secret, which is why the
rollout above runs it immediately after `migrate`. Re-run that one service
after every migration, because the grants only cover the tables that exist
when it runs. Review the template before the first rollout; do not apply it by
hand. Never give API or worker `kip_owner` or `BYPASSRLS`.

`compose.production.yaml` never sets a password on these roles: they stay
NOLOGIN groups and the deployment binds its own logins (`GRANT kip_api TO
<login>`). Backup is the exception. BYPASSRLS is a role attribute and role
attributes are not inherited through role membership, so a login that is only
a member of `kip_backup` still cannot set `row_security=off` and would take a
silently workspace-filtered dump. The backup process must log in as
`kip_backup` itself, or as a login created with BYPASSRLS.

The reference API binds only to loopback. Terminate TLS and verify organization
identity in an identity-aware proxy before forwarding the Bearer JWT. The
PostgreSQL network is internal and the NAS bind is read-only. Validate the
resolved Compose model before every rollout and reject a deployment if a
secret, source path, or image digest is still an example value.

The optional MCP edge is a stdio-only MCP 2.x server. After an MCP SDK major
upgrade, start `./scripts/mcp.sh` through a real client and verify
initialization, tool discovery, `kip_capabilities`, the reported KIP version,
and the `kip.envelope.v1` result. Streamable HTTP transport is not enabled;
adding it requires a separate identity, TLS, origin, request-size, and
deployment design rather than exposing the stdio server on a port.

`${KIP_NAS_PATH}` is required and is bind-mounted read-only into **both** the
worker and the API service. The API opens the live source path for evidence
freshness checks and `xlsx-read` range reads; without the bind every unit
reports stale and answers silently drop evidence. All three application
services carry resource limits, the API and worker have container
healthchecks (the worker check proves PostgreSQL reachability with the
worker's own credentials), and the API healthcheck targets `/readyz`, which
performs a real database round-trip and answers 503 when PostgreSQL is
unreachable. `/healthz` remains a process-liveness probe only.

## Online source package ZIP

Use this path for a source handoff to an internet-connected developer or agent.
It is deliberately smaller than the production release bundle and does not
contain wheels, images, SBOM, provenance, deployment secrets, local state, or
private evaluation data.

```bash
./scripts/build-package.sh
./scripts/verify-package.sh dist/kip-$(cat VERSION).zip
(cd dist && shasum -a 256 -c kip-$(cat ../VERSION).zip.sha256)
```

The builder refuses an existing output and a dirty source tree. Use
`--allow-dirty` only for a named local candidate; the manifest records that
state. A recipient verifies the external digest and ZIP before extraction,
then runs `./scripts/bootstrap.sh` and `./scripts/verify.sh` inside the single
versioned root. Do not substitute this source ZIP for the signed production
bundle described below.

## Release artifacts

Run the local gates and build a reproducible handoff bundle from a clean tree:

```bash
uv export --frozen --no-dev --no-emit-project --extra postgres --extra api \
  --extra identity --extra extractors --extra mcp \
  --output-file requirements/runtime.txt
uv run pytest tests/test_release_bundle.py::test_runtime_lock_contains_every_core_dependency
uv run pip-audit --requirement requirements/runtime.txt --disable-pip
make audit
make coverage
export KIP_API_IMAGE='registry.example/kip@sha256:<verified-digest>'
export KIP_WORKER_IMAGE="$KIP_API_IMAGE"
export KIP_MIGRATE_IMAGE="$KIP_API_IMAGE"
make release
make verify-release BUNDLE="dist/kip-$(tr -d '[:space:]' < VERSION)"
make verify-release BUNDLE="dist/kip-$(tr -d '[:space:]' < VERSION).tar.gz"
```

The verifier checks the manifest, checksums, wheel, SPDX SBOM, SLSA provenance
statement, image lock, required package files, private-path patterns, and
forbidden secret/data artifacts. The private reviewed golden corpus
(`evaluation/golden/private-onedrive-nl.yaml` and its floor file) is excluded
from the package copy and its presence in a bundle fails verification; the
redacted `private-starter.yaml` sample ships intentionally. A branch or pull-request workflow produces a
candidate only. A `v$(cat VERSION)` tag whose value exactly matches `VERSION`
is the sole GitHub workflow that pushes the immutable GHCR image and creates
GitHub provenance and wheel-SBOM attestations. Verify published subjects with
the repository identity before deployment, for example:

```bash
gh attestation verify dist/release.tar.gz --repo OWNER/REPOSITORY
gh attestation verify dist/wheels/*.whl --repo OWNER/REPOSITORY
```

Do not deploy a `local/kip` candidate reference. Record the release archive
SHA-256, GHCR digest, attestation verification, migration result, and rollback
digest in the change record.

## Backup

로컬/개발 설치에서는 별도 설정 없이 그대로 실행하면 됩니다. `.env`의
`KIP_DATABASE_URL`과 기본 경로(`var/backups`, `var/cas`)를 사용합니다.

```bash
./scripts/backup.sh --retain 7
```

프로덕션에서는 비밀 파일과 실제 경로를 지정합니다.

```bash
export KIP_DATABASE_URL_FILE=/run/secrets/backup-database-url
export KIP_CAS_PATH=/srv/kip/cas
export KIP_BACKUP_PATH=/srv/kip/backups
./scripts/backup.sh --retain 7
```

The backup set contains:

- PostgreSQL custom-format dump with `row_security=off`;
- canonical JSONL export and database row/migration/extension/RLS manifest;
- CAS archive plus content-hash manifest;
- ontology and configuration snapshot;
- sealed manifest and SHA-256 checksums.

The configuration snapshot copies `config/*.toml` and `config/*.yaml` through
a redaction pass: a quoted literal value assigned to a secret-looking key
(`password`, `secret`, `token`, `credential`, `api_key`, `access_key`,
`private_key`) is replaced with `[REDACTED]` unless the value is an
`env:`/`file:` reference or the key uses the `*_env`/`*_file` indirection
convention, and URL userinfo passwords are redacted anywhere they appear.
Sealing and verification then rescan the archived configuration and fail the
backup if a literal secret survived. This protects only convention-following
configuration keys; it is not a general secret scanner, so keep secrets out of
TOML values in the first place.

Run backup as the dedicated, audited `kip_backup` login itself, or as the
database owner. Membership is not enough: BYPASSRLS is a role attribute and
role attributes are not inherited through role membership, so a login that is
only a member of `kip_backup` cannot set `row_security=off` and its dump would
be silently filtered to one workspace. That role needs full read access and
verified `BYPASSRLS`; never reuse API or worker credentials. On the Compose
deployment, `deploy/apply-roles.sh` gives `kip_backup` a password from
`KIP_BACKUP_DB_PASSWORD` and `scripts/bootstrap_env.py` writes the matching
`KIP_BACKUP_DATABASE_URL` into `.env`. The script writes a private partial
directory first and atomically publishes it only after every artifact is
sealed. A retained `.partial-*` directory with `FAILED` is incident evidence,
not a usable backup.

`--retain N` (default 7, also `KIP_BACKUP_RETAIN`) prunes the oldest sealed
backup sets after a successful run so at most N remain. Only directories named
like a backup timestamp that contain `backup-manifest.json` are pruning
candidates; `.partial-*` incident evidence and unrelated files are never
touched.

The local archive is not encrypted or uploaded by KIP. On the macOS host,
`install-launchd.sh` schedules the daily backup and retention (see
"Host scheduling"); still use the platform backup service to encrypt the
archive, copy it off-host, and apply legal retention. `ops-report.sh` alerts
when the newest sealed backup is missing or older than 26 hours. Never put a
database URL or encryption key in the archive or repository.

## Host scheduling (macOS launchd)

```bash
./scripts/install-launchd.sh 900 --retain 7 --with-ops-report
./scripts/install-launchd.sh --dry-run   # render plists without installing
./scripts/uninstall-launchd.sh
```

The installer manages up to five user launch agents: the host worker
(`com.kip.knowledge-fabric.worker`), the periodic sync enqueue
(`com.kip.knowledge-fabric.sync`), the daily sealed backup with retention
(`com.kip.backup`, 03:15 local by default, `--backup-hour` to change,
`RunAtLoad` disabled), the semantic model runtime (`com.kip.semantic`,
`semantic-server.sh run` with `KeepAlive`, added only when
`var/semantic-venv` is installed and `KIP_SEMANTIC` is not `off`; the installer
stops a server started by `app-up.sh` so launchd owns the port), and — only
with `--with-ops-report` — the periodic `ops-report.sh` run
(`com.kip.ops-report`, `--ops-interval`, default 1800s). Linux hosts use the
`deploy/systemd/kip-semantic.service` user-unit template for the same runtime.

The installer resolves the configuration once at install time — an explicit
`KIP_CONFIG`, else `config/kip.host.generated.toml` when guided setup
produced one, else `config/kip.toml` — prints the choice, and bakes it into
every plist's `EnvironmentVariables`, so launchd jobs run the same
consented configuration as the interactive surfaces. Re-run the installer
after re-running guided setup to pick up a new generated config.

Before installing the host worker the installer refuses if a Docker Compose
worker service is already running, because two workers would double-process
the same queue; pass `--allow-compose-worker` only when that is intended.

Launchd job logs append under `var/log/launchd-*.log`. The installer renders a
newsyslog rotation policy (5 rotations, 10 MB, compressed) to
`var/newsyslog.kip.conf` and prints the one `sudo install` command that
activates it under `/etc/newsyslog.d/kip.conf`; rotation is not active until
that command is run. `--dry-run` renders every plist under
`var/launchd-preview/` and installs nothing.

### Isolated restore

Restore refuses a database with user tables, an existing evidence directory,
or a non-empty CAS target. Use a database and CAS path distinct from production:

```bash
export KIP_RESTORE_CONFIRM=YES
export KIP_RESTORE_DATABASE_URL_FILE=/run/secrets/empty-restore-database-url
export KIP_RESTORE_CAS_PATH=/srv/kip/restore/cas
export KIP_RESTORE_EVIDENCE_PATH=/srv/kip/restore/evidence-$(date -u +%Y%m%dT%H%M%SZ)
./scripts/restore.sh /srv/kip/backups/20260810T000000Z
```

Restore verifies the sealed backup before writing, restores in one PostgreSQL
transaction, compares database and CAS manifests, runs append-only migrations,
rebuilds the lexical projection, analyzes the database, verifies lexical and
graph projections, and emits a checksummed receipt. It never cleans or deletes
an existing target. The restore statement timeout defaults to 300 seconds and
can be raised only through `KIP_RESTORE_STATEMENT_TIMEOUT_MS` for a measured
large-corpus drill.

## Restore drill

Run quarterly and after a PostgreSQL major upgrade, migration-set change, or
release that changes persistence or extraction. First produce a fingerprinted
baseline report against the source deployment, then use an empty drill target:

```bash
export KIP_RESTORE_DRILL_CONFIRM=YES
export KIP_DRILL_DATABASE_URL_FILE=/run/secrets/empty-drill-database-url
export KIP_DRILL_CAS_PATH=/srv/kip/drills/2026-q3/cas
export KIP_DRILL_GOLDEN_DATASET=/srv/kip/evaluation/golden.yaml
export KIP_DRILL_BASELINE_REPORT=/srv/kip/evaluation/baseline/latest.json
export KIP_DRILL_REVIEW_BUNDLE=/srv/kip/evaluation/reviews.yaml  # optional
export KIP_DRILL_REPORT_PATH=/srv/kip/drills/2026-q3/report
make restore-drill BACKUP_DIR=/srv/kip/backups/20260810T000000Z
```

The drill compares decision-relevant retrieval, ACL/integrity, answer, and
ontology metrics using the exact dataset and optional review bundle supplied to
the run. Keep its receipt and checksum set with the release record. A successful
SQL restore without CAS parity, projection verification, and golden comparison
does not pass the recovery gate.

## Projection rebuild

Rebuild lexical, vector, and graph projections independently. Never delete approved assertions to rebuild a projection.
Sync and activated re-extraction keep the semantic projection current (see
[Semantic search](#semantic-search-default)); `kip projection rebuild --name
semantic` resumes or repairs it.

## Ontology mining and review

Guided setup asks for `relation_mining_mode` after the generation-provider and
provider-specific egress questions. `enabled` writes
`[models.relation_mining].enabled=true` to both generated configs; `disabled`
writes false. Enabling mining while `models.generation` is disabled is rejected
before a setup plan can be created. A resumed pre-change setup state receives
this question before it can produce a new plan.

Enable `[models.relation_mining]` only after the generation destination and
egress policy pass the target corpus review. Mining is never triggered by
search or answer requests.

When raising the mining batch caps (`max_units`, `max_characters`, and the
proposal limits), review the generation client knobs alongside them: a
larger batch sends a proportionally larger request and can need more than
the default `models.generation.timeout_seconds = 60` to complete, and a
provider returning more proposals can approach
`max_response_bytes` (default 1 MiB). Both are configuration keys; raise
them together with the batch caps rather than after the first timeout.

Mining, entity creation, and every approve/reject/revoke require the **admin
role**, and `--role` is a global option that must come **before** the
subcommand (`./scripts/kip --role admin ontology mine ...`, not
`./scripts/kip ontology mine ... --role admin`). Export `KIP_ROLES=admin`
instead if you run many of these in one session.

```bash
./scripts/kip ontology entities
./scripts/kip --role admin ontology mine --unit-id UNIT_ID
./scripts/worker.sh --once
./scripts/kip ontology candidates --status proposed
./scripts/kip --role admin ontology entity-approve ENTITY_CANDIDATE_ID
./scripts/kip --role admin review approve RELATION_CANDIDATE_ID
```

Submit small, coherent evidence batches. A job is idempotent for workspace,
principal access snapshot, ontology release, miner revision, and sorted unit
IDs. If the captured dynamic access snapshot expires before a worker claims the
job, processing fails closed and must be resubmitted after re-authentication.
Model output remains a candidate even for low-risk predicates.

Normal answers use `[ontology.answer_context]` bounds. `entity_limit` caps name
resolution, `edge_limit` caps approved edges, and `max_depth` caps paths. These
are read-only query limits; raising them never enables candidates or historical
assertions. Inspect the same context independently with:

```bash
./scripts/kip ontology context "A과제 변경 승인"
```

## Ontology release migration

Validate both immutable release roots and the reviewed manifest before creating
any target-version candidate:

```bash
./scripts/kip ontology validate --root releases/ontology-1.0.0
./scripts/kip ontology validate --root releases/ontology-2.0.0
./scripts/kip ontology diff \
  --before releases/ontology-1.0.0 \
  --after releases/ontology-2.0.0 \
  --migration ontology/migrations/1.0.0-to-2.0.0.yaml
./scripts/kip ontology migrate-materialize \
  --before releases/ontology-1.0.0 \
  --after releases/ontology-2.0.0 \
  --migration ontology/migrations/1.0.0-to-2.0.0.yaml
./scripts/kip review list --status proposed
```

`[ontology.migrations].max_assertions` bounds each run. Materialization first
validates every target relation and reopens every evidence unit, then creates
deterministic candidates. Rerunning is safe. Source assertions remain active;
candidate approval and any later source supersession are separate audited
decisions. Approval remains blocked until the target ontology is the active
application contract, and normal graph traversal reads only that active
version. Use a dedicated entity identity migration workflow if a release changes
a type that already has live entities.

## Adaptive ontology discovery and interaction memory

The starter default is `ontology.domain_profile = "empty"`, and the shipped
example and container configurations enable both
`ontology.adaptive_discovery` and `interaction.enabled`. Guided setup still
records the `disabled`/`explicit_consent` consent decision and writes the
chosen mode. This feature does not run during normal search, answer, sync, or
mining.

Ask a bounded follow-up question and persist a selection only when the caller
explicitly asks to remember it:

```bash
./scripts/kip interaction clarify \
  --reason scope_selection \
  --prompt "어느 문서 범위를 기본 검색으로 사용할까요?" \
  --choices-json '[{"id":"onedrive","label":"OneDrive"}]' \
  --no-allow-freeform \
  --preference-key default_source_scope
./scripts/kip interaction answer --question-id CLRQ_ID --option-id onedrive --remember
./scripts/kip interaction preferences
./scripts/kip interaction forget --key default_source_scope
```

Feedback is bounded and does not accept raw query or answer text:

```bash
./scripts/kip interaction feedback \
  --outcome not_helpful \
  --reason-code wrong_scope \
  --reason-code missing_evidence
```

An ontology observation remains a candidate even after review:

```bash
./scripts/kip ontology discovery propose \
  --kind entity_type --symbol contract --label "계약" \
  --definition "업무상 체결하는 계약을 표현한다." --confirmed
./scripts/kip --role admin ontology discovery list --status proposed
./scripts/kip --role admin ontology discovery review \
  --candidate-id ODC_ID --action accept
```

Listing and reviewing discovery candidates both require the admin role
(`--role admin` before the subcommand, or `KIP_ROLES=admin`); proposing does
not.

Accepting an `entity_type` or `predicate` candidate materializes an additive
ontology release automatically (ADR-044): the symbol is written into the
YAML tree (entity types into the active domain profile, predicates into
`core/predicates.yaml` with review-policy sync), the file version bumps
minor, and the review response reports the released file and version.
Auto-released predicates default to `review: required`/`risk: high`, so
assertions using them still need exact evidence and human review. Restart
the API, worker, and MCP processes to load the new catalog
(`catalog_refresh: "restart_required"`); per-invocation CLI commands see it
immediately. Both Compose profiles bind-mount the version-controlled
ontology checkout (`KIP_ONTOLOGY_PATH`, default `./ontology`) read-write
into the API — the admin review surface that writes releases — and
read-only into the worker; the host directory must be writable by uid
10001, and released changes stay reviewable through git history. A missing
or read-only mount fails closed with an actionable error.
`controlled_value` and `alias` candidates stay status-only; use the manual
release and migration workflow for those. Breaking changes (rename, merge,
deprecate) still go through the preceding migration workflow. Schedule
`./scripts/kip interaction prune` at least daily when interaction persistence
is enabled; it deletes only expired clarification rows in the active workspace.
MCP reviewers must set `KIP_ROLES=admin`; normal users do not receive reviewer
privileges merely by using MCP.

## Startup configuration and logging

A database URL is required: `KIP_DATABASE_URL` (or `KIP_DATABASE_URL_FILE`, or
whatever `database.url_env` names). Only `KIP_ENV=test` may fall back to the
non-durable in-memory repository; every other environment now fails at startup
with a configuration error naming the missing variable instead of quietly
booting a repository that loses every ingest at exit.

Configuration keys the running build does not read — a misspelling, or a key
left behind by an older release — never fail startup, but `capabilities`
reports them once in `warnings`, next to the non-durable repository warning.
Check it after editing a config or upgrading:

```bash
./scripts/kip capabilities | jq '.data.warnings'
```

The CLI, the API app and the worker each configure logging at startup from
`app.log_level` (`KIP_LOG_LEVEL` overrides it). Records are one JSON object
per line on stderr, so the CLI's stdout stays a parseable envelope; redirect
stderr to collect them (`./scripts/kip status 2>>var/log/kip-cli.log`).

## Redacted RAG tracing

Query tracing is enabled by default and persists only the versioned redacted
decision contract. Inspect it through an administrative surface:

```bash
./scripts/kip telemetry traces --limit 100
./scripts/kip telemetry traces --request-id req_OPAQUE_ID
./scripts/kip telemetry prune
```

`telemetry.retention_days` defaults to 30. Schedule `telemetry prune` daily;
the command deletes only expired rows in the active workspace. REST operators
use `GET /v1/admin/query-traces` and
`DELETE /v1/admin/query-traces/expired`, both behind the normal admin identity
gate.

### Semantic search (default)

Since 3.12.0 (ADR-065) the shipped configs and setup-generated configs set
`search.semantic_enabled = true`, `search.default_mode = "hybrid"` (lexical
and vector candidates fused by reciprocal rank) and
`search.semantic_auto_activate = true`, with `[models.embedding] enabled = true`
serving Qwen/Qwen3-Embedding-0.6B as `kip-qwen3-embedding-0.6b` at revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024 dimensions. The supported
PostgreSQL profile includes pgvector through `0006_pgvector_1024_projection.sql`
and the 1024-dimensional HNSW index through `0018_embeddings_1024_hnsw.sql`.
Migration 0018 uses a transaction-local unlimited statement timeout for index
construction; normal query timeouts remain unchanged. A deployment with
`search.semantic_enabled = false` stays lexical-only.

`hybrid` is the default because it measured best on the reviewed private set
(Recall@10/MRR 89.5%/89.5%, P95 2.42 s). BM25 reranking of the fused list
lowered quality (78.9%/59.8%, below lexical), and the BGE cross-encoder raised
recall but not ranking quality at 3-4x the latency on the reference Mac (P95
7.2 s with 20 candidates, 16.2 s with 40). The BM25 reranker stays on for the
lexical path (`search.lexical_rerank_enabled = true`,
`models.reranker.backend = "bm25"`): lexical mode and the lexical fallback
rerank with it. `reranked` remains an explicit mode. To rerank with the BGE
reranker-v2-m3 cross-encoder, set `models.reranker.backend = "http"` and
`models.reranker.max_document_chars = 2048` (the shipped 8000 is the BM25 cap;
add `search.default_mode = "reranked"` if the default path should rerank) and start
the runtime with `KIP_SEMANTIC_RERANKER=on`, which loads the reranker model
(about 2 GB more memory). The cross-encoder then scores only the fused
`reranked` pool: the BM25 lexical reranker stays on for lexical mode and the
lexical fallback, so neither waits on nor fails with the model runtime. It
is configured by the optional `[models.lexical_reranker]` table (see
[Local lexical reranking](#local-lexical-reranking)).

#### Model runtime

`./scripts/bootstrap.sh` installs the isolated runtime into `var/semantic-venv`
(Infinity 0.0.77 from the hash-locked `requirements/semantic.txt`, compiled from
`requirements/semantic.in` and installed with `uv pip sync --require-hashes`)
and prefetches the pinned embedding snapshot into `var/model-cache` (about
1.2 GB, plus about 2.3 GB for the reranker when `KIP_SEMANTIC_RERANKER=on`) with
`./scripts/semantic-server.sh prefetch`. `KIP_SEMANTIC=off`
(environment for the installer or bootstrap, persisted into `.env`) skips it,
and hosts with less than 8 GiB of RAM skip it automatically. A failure never
fails bootstrap: search stays lexical and the retry command is printed:

```bash
./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch
```

A freshly created `config/kip.toml` gets `semantic_enabled = false` when the
runtime was not installed. Guided setup records `semantic_search` in the plan
from whether `var/semantic-venv/bin/infinity_emb` exists and `KIP_SEMANTIC` is
not `off`, and generates matching host and container configs; a lexical-only
plan drops the compose `models` service and warns why.

```bash
./scripts/semantic-server.sh start    # background; also run by app-up.sh
./scripts/semantic-server.sh wait     # block until GET /models answers
./scripts/semantic-server.sh status
./scripts/semantic-server.sh stop     # waits up to 20 s, then force-stops
./scripts/semantic-server.sh run      # foreground, for a supervisor
./scripts/semantic-smoke.sh
```

`./scripts/app-up.sh --database-only` starts the runtime and waits until it is
ready. For CI, agent runners, containers, launchd, systemd, or another
supervisor, use `semantic-server.sh run` as the supervised foreground process;
detached child lifetime is not guaranteed after an ephemeral runner command
returns. `./scripts/install-launchd.sh` adds the supervised `com.kip.semantic`
item and `deploy/systemd/kip-semantic.service` is the Linux user-unit template.
Readiness is `GET http://127.0.0.1:7997/models` with the embedding model (and
the reranker when `KIP_SEMANTIC_RERANKER=on`) listed. The server binds to
loopback only and runs offline (`HF_HUB_OFFLINE`) once the pinned snapshots are
cached. `start` does nothing when a supervised (launchd/systemd) or other
instance already runs, answers or holds the port (a compose `models` container
that is still loading holds it before it answers); with
`KIP_SEMANTIC_RERANKER=on`, `start` and `wait` fail if that instance does not
list the reranker, and the instance must be stopped and started again. Model
probes time out after 5 seconds. `run` never loads a second copy either: while
another runtime runs (even while loading), answers or holds the port it logs once,
re-checks every `KIP_SEMANTIC_RECHECK_SECONDS` (60), and takes over when the
port is free, so launchd `KeepAlive` and systemd `Restart` do not keep
reloading the models. `wait` also follows a supervised instance and points at
`var/log/launchd-semantic.err.log` or `journalctl --user -u kip-semantic`.
`status` exits 0 while a runtime process of the checkout runs, even before it
answers. `stop` stops the instance that `start` launched; for a supervised
instance it prints the `launchctl bootout` or `systemctl --user stop` command
instead. It waits up to
`KIP_SEMANTIC_STOP_SECONDS` (20) for in-flight requests and then force-stops
(after re-checking that the PID is still the runtime), so a busy server cannot
be orphaned with its model memory.

The device is detected automatically: Apple Silicon uses `mps`, a visible
NVIDIA GPU uses `cuda`, anything else `cpu` (override `KIP_SEMANTIC_DEVICE`).
The dtype is float16 on a GPU and float32 on CPU (override
`KIP_SEMANTIC_DTYPE`); float16 and float32 embeddings measured a cosine of at
least 0.99998. On `mps`, `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.5` and
`PYTORCH_MPS_LOW_WATERMARK_RATIO=0.3` cap the Metal allocator cache unless the
environment sets them. On the reference Apple Silicon (24 GB, float16,
embedding model only) the runtime holds about 2.7 GB; with both models and long
inputs it had reached 10-15 GB, which is why the reranker is opt-in, inputs are
capped at 4000 characters, and the Metal allocator cache is capped. The
isolated environment pins Infinity 0.0.77 and
Click 8.1.8 (Click 8.4.x is incompatible with Infinity's Typer 0.12.5 dual
boolean flags); Apple MPS runs with BetterTransformer disabled because
Infinity's optional Optimum precheck is invalid on that path. Server batches of
four embedding inputs and two reranking pairs fit the validated 24 GB Apple
Silicon profile.

Infinity answers `/embeddings` and `/rerank` with HTTP 200 for a model name it
does not serve, embedding with whatever it loaded, so before its first request
(and every 300 seconds after, to catch a restart) the embedding adapter checks
`GET /models` for `models.embedding.model`, and the HTTP reranker for
`models.reranker.model`. A runtime that advertises another name — a
`KIP_EMBEDDING_SERVED_MODEL` (or `KIP_RERANKER_SERVED_MODEL`) pointing
elsewhere, a `models.embedding.model` edited without restarting the runtime, or
a stale launchd/systemd unit — is treated as unavailable: default-mode search
degrades to lexical with `semantic_degraded` instead of embedding queries in a
space the active projection was never built with.

This is a served-name check only. `GET /models` on Infinity 0.0.77 reports no
revision and no weight hash, so **other weights or another revision served
under the configured name are not detected**, and
`models.embedding.revision` stays unverified at runtime; it is enforced only as
part of the embedding-space identity in the database. Because
`--model-id`/`--revision` and `--served-model-name` are independent,
`scripts/semantic-server.sh` refuses to start when `KIP_EMBEDDING_MODEL` or
`KIP_EMBEDDING_REVISION` (and, with `KIP_SEMANTIC_RERANKER=on`,
`KIP_RERANKER_MODEL` or `KIP_RERANKER_REVISION`) differ from the pinned
defaults without a matching `KIP_EMBEDDING_SERVED_MODEL` /
`KIP_RERANKER_SERVED_MODEL`: evaluating a candidate model means giving it a
name of its own and setting `models.embedding.model` (or
`models.reranker.model`) to that name.

`./scripts/kip doctor` reports a non-required `semantic_search` check with the
runtime reachability, the projection state including the full completeness
count (`stale` when the active space is missing units), and the command that
fixes it:
`./scripts/semantic-server.sh start` when the runtime is down,
`./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch` when
it is not installed, and `kip sync run --source SOURCE` or
`kip projection rebuild --name semantic` when the projection is missing,
shadow, or stale. A reachable runtime that serves a different model is reported
separately from an unreachable one (and from a runtime that answers while
serving nothing yet): the reason names the served ids and the configured
`models.embedding.model`, and `models.reranker.model` when
`models.reranker.backend = "http"`. The probe goes through the same egress
allowlist as a query, so a `base_url` that `security.allow_remote_model_egress`
forbids is reported instead of being connected to.

#### Embedding settings

```toml
[models]
circuit_cooldown_seconds = 30

[models.embedding]
enabled = true
batch_size = 32
max_batch_chars = 16000
max_document_chars = 4000
timeout_seconds = 120
query_timeout_seconds = 10
```

Each document input is bounded to `models.embedding.max_document_chars`
(default 4000) with the versioned `head_tail_v1` strategy, preserving the title
and sampling both ends of oversized units. At the former 12000 the runtime
reached a 15 GB footprint on a 24 GB Mac and swapped heavily. A rebuild sends at
most `batch_size` units per request, bounded by `max_batch_chars` characters,
so one request never holds the runtime long enough to starve interactive query
embeddings; query embeddings have their own 10-second timeout with a 3-second
connect timeout. After a runtime failure a per-adapter circuit skips it for
`models.circuit_cooldown_seconds` so queries do not each wait for a timeout.
The embedding cap, strategy, model, revision, dimensions, and query
instruction are the embedding-space identity; changing any of them creates a
new shadow space instead of mixing incompatible vectors. The opt-in HTTP
reranker (`models.reranker.backend = "http"`, BGE reranker-v2-m3 on the same
runtime with `KIP_SEMANTIC_RERANKER=on`) caps its input with
`models.reranker.max_document_chars` (2048 when unset).

#### Projection maintenance

Every sync (`kip sync run`, REST `POST /v1/sync/filesystem/{source}?enqueue=false`,
worker `sync.source` jobs) and every activated `parser reextract` embeds new or
changed units. When the projection is complete and its identity is in
`RELEASE_REVIEWED_EMBEDDING_IDENTITIES` (the pinned default above: provider
`infinity`, model, revision, 1024 dimensions, 4000 characters, `head_tail_v1`,
and the default query instruction), it is activated automatically;
`kip projection rebuild --name semantic` does the same for a complete reviewed
space. Other identities are completed but wait for evaluation and
`kip projection activate` (ADR-036/037). `search.semantic_auto_activate = false`
restores fully manual activation.

Sync and re-extraction summaries carry an optional `semantic_projection`
object (`status` `disabled|current|updated|incomplete|unavailable`,
`space_id`, `newly_indexed_units`, `indexed_units`, `content_units`,
`active`, `activated`, `reason`); the reason is also added to `warnings`. CLI
syncs print `semantic projection: embedded N/M new or changed units` on
stderr. A runtime that is down during sync yields `unavailable`, not a sync
failure; the next sync resumes. Rebuild is resumable: it pages through pending
current ACL-fresh units 1000 at a time (`models.embedding.page_size`) and
embeds only units whose vector is missing or whose source hash changed.
`projection verify` uses that same current-unit set as its denominator,
accepts a complete `shadow` or `active` space, and ignores vectors from
inactive extractions or superseded revisions. `projection activate` refuses an
incomplete space.

The first full projection of a large corpus is slow. On the reference Apple
Silicon (24 GB) throughput was about 100 short units per second and roughly
2,800-5,000 characters per second for longer units; a 1,912-file OneDrive
corpus with about 176,500 units (about 44 M characters at the 4000 cap) needs
on the order of three hours. Search stays lexical, with a `semantic_degraded`
warning, until the projection is complete and active.

#### Degradation

Default-mode search falls back to the lexical path (BM25-reranked) with the
envelope warning `semantic_degraded` when the runtime or the active projection
is unavailable, including a runtime that answers but serves a model other than
`models.embedding.model`. When a deployment sets
`search.default_mode = "reranked"` and
only the reranker fails, it keeps the fused lexical+vector ranking with
`rerank_degraded`; `lexical_rerank_degraded` still marks a failed lexical
reranker and also a fallback left in lexical order because
`search.lexical_rerank_enabled = true` is set without a reranker
(`models.reranker.enabled = false`). These warnings
appear in the search and context envelope `meta.warnings` on CLI, REST, and
MCP, not only in traces, and they survive an empty result: a degraded run that
matched nothing reports both its degradation and `no_visible_indexed_units`.
A bundle cut to the requested budget adds `context_truncated` (the envelope
name for `ContextBundle.truncated`), and a request whose retrieval raised
carries `search_failed` in its `ok: false` envelope. Public v1 `SearchRequest.mode` accepts `lexical`,
`vector`, `hybrid`, and `reranked`; an explicit `--mode vector|hybrid|reranked`
request fails instead of degrading. `capabilities` stays cheap because MCP
clients call it first: `semantic_search` is true and `semantic_projection_status`
is `active` when the active space matches the configured identity
(otherwise `incompatible`, `shadow`, or `missing`). The full completeness count,
with a `stale` verdict when the active space is missing units, is reported by
`kip doctor` and `kip projection verify`; every sync keeps the projection
current.

The reference HNSW query settings are:

```toml
[search]
hnsw_ef_search = 200
hnsw_max_scan_tuples = 100000
```

Each vector query also sets `hnsw.iterative_scan=strict_order` transaction
locally. Change these bounds only with an exact-search recall comparison and
filtered ACL/freshness candidate-sufficiency evidence. Projection queries
evaluate ACL freshness and source policy once per artifact and snapshot rather
than per unit; per-unit checks had exceeded the 15-second statement timeout at
about 176k units. Projection statements (pending pages, progress counts, scope
listing, embedding upserts) use `database.projection_statement_timeout_ms`
(default 300000) instead of the interactive `database.statement_timeout_ms`,
because a multi-hour first build had hit the interactive limit.

#### Adopting the default on an existing deployment

`kip update` keeps each deployment's config, so a config with
`semantic_enabled = false` stays lexical. To adopt the default:

```bash
./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch
# set search.semantic_enabled = true, search.default_mode = "hybrid" (the
# earlier example value "reranked" BM25-reranks the fused list and measured
# lower) and [models.embedding] enabled = true with the defaults above (or
# re-apply guided setup)
./scripts/semantic-server.sh start
./scripts/kip sync run --source SOURCE   # or: ./scripts/kip projection rebuild --name semantic
```

The reviewed space activates itself when complete. A config that keeps
`max_document_chars = 12000` forms a different, unreviewed identity and needs
evaluation and manual activation. The 3.12.0 measurements are recorded in
`docs/IMPLEMENTATION_STATUS.md` and `docs/RAG_EVALUATION.md`.

### Periodic public scorecard

Enable the `public-government` source in the local `config/kip.toml` (the
embedding adapter and the BM25 reranker are on by default since 3.12.0; the BGE
`reranked` variant also needs `models.reranker.backend = "http"` and
`KIP_SEMANTIC_RERANKER=on`), then run:

```bash
make fetch-corpus
./scripts/kip sync run --source public-government
make evaluate
```

Timestamped JSON and Markdown scorecards are written below
`evaluation/reports/public-government/`. `latest.*` is a convenience pointer;
`evaluation/reports/evolution.jsonl` is the append-only metrics trail. Do not
use `python -m kip.cli` directly for this workflow because it does not load
`.env`. The default scorecard performs one untimed full-dataset warmup pass per
variant, suitable for the persistent model sidecar. Pass `--warmup-passes 0`
to `evaluate run` only for a deliberate cold-start measurement.

### Merge and private-corpus regression gates

```bash
./scripts/golden-gate.sh --portable
./scripts/golden-gate.sh --private
KIP_REQUIRE_PRIVATE_GOLDEN=1 ./scripts/golden-gate.sh --private
```

`./scripts/golden-gate.sh` is the supported wrapper and `verify.sh` uses it; the
former `./scripts/golden_gate.py` and `./scripts/portable_golden_gate.py`
invocations were not executable and ran outside the project interpreter.

The portable gate expands the checked-in 20-document manifest into 100 positive
query contracts and 20 ACL-negative cases. It always runs in hosted CI and
protects search stages, filters, envelope behavior, and authorization without
shipping private data. It runs both lexical and the shipped default mode, the
latter with a deterministic character-bigram hashing embedding so no model is
needed, and requires recall and MRR 1.0, zero ACL leaks, and P95 at most
100 ms for each. It is synthetic contract evidence, not a production quality
score.

The private gate uses the approved real corpus. Its floor file supports
per-variant floors: `hybrid` (recall 0.84, MRR 0.84, P95 at most 8000 ms) and
the `lexical` fallback (recall 0.84, MRR 0.60, P95 at most 8000 ms). It fails when
the reviewed corpus is indexed but semantic search is configured and not
ready; when a deployment disables semantic search, the semantic floors are
skipped with a message. It also fails when the deployment's own default mode
(`search.default_mode`, or `lexical` when semantic search is disabled) has no
floor, and when the floor file's `variants` object is empty or holds a
malformed entry. Developer environments may skip it when that corpus is
intentionally absent. Protected corpus-bearing runners
must set `KIP_REQUIRE_PRIVATE_GOLDEN=1`; missing dataset, empty repository, or a
skip then fails closed. Do not report a merge as private-corpus-gated unless that
protected job actually ran.

## Parser upgrade

Parser improvements only benefit newly extracted content: documents indexed
before an upgrade keep their previous units and locators until re-extracted
through the shadow/atomic-activation path. After the 2026-08-15 parser
upgrades (structural DOCX units and paragraph locators, structural CSV rows,
encoding-aware text quality), re-sync or re-extract existing DOCX/CSV
sources to pick up the richer units.

Prepare HWP/HWPX candidates without changing the active index:

```bash
./scripts/kip parser reextract --source company-nas
```

To re-extract another format after its parser changes, name it with the
repeatable `--extension` option (for example `--extension .pdf`; the leading
dot is optional and matching is case-insensitive). An extension without a
registered parser is rejected with a validation error, and the summary lists
the scanned `extensions`.

Review `eligible`, `parsed`, `rejected`, `failed`, `unit_count`,
`parser_counts`, and every warning in the versioned JSON envelope. Compare a
reviewed golden corpus and exact evidence before activation. Then run the
separate mutation command:

```bash
./scripts/kip parser reextract --source company-nas --activate
```

The operation scans and hashes only configured files with the selected
extensions (HWP/HWPX by default). Each candidate inherits the current canonical
source ACL snapshot, scopes, and classification;
parser re-extraction never applies a configuration policy change. Use normal
source synchronization to change ACL policy. Activation then checks the
current artifact revision, source hash, canonical access controls, and minimum
parser quality immediately before the transaction. It inserts a new extraction
and its units, swaps the active lexical rows, and deactivates the previous
extraction in one PostgreSQL transaction. The previous extraction remains
recoverable history. A rejected, failed, changed, stale, or unauthorized
candidate leaves the old active extraction intact. Normal search and
incremental sync never trigger this workflow.

## XLSX exact-range reads

Use `xlsx-read` only after the workbook has been indexed and keep freshness
checking enabled for material claims:

```bash
./scripts/kip xlsx-read ARTIFACT_ID --sheet "정산" --range "A1:F40"
```

The response shape exactly matches the requested rectangle, including blank
cells. Reads reject reversed ranges, coordinates beyond `XFD1048576`, and
requests above 100,000 cells; split a rejected large request into explicit,
reviewable ranges. Date/time values are ISO 8601 strings, durations are ISO
8601 duration strings, and Excel serials plus number formats remain adjacent.
Formula source and cached values are separate, and KIP does not recalculate the
workbook. Treat a cached result as workbook state that may be stale, not as a
fresh calculation.

## Filesystem parser resource isolation

Reference configurations enable `[parsers.isolation]`. Each NAS document is
parsed serially in a fresh child with the following M4 Pro 24 GB defaults:

```toml
[parsers.isolation]
enabled = true
wall_seconds = 180
cpu_seconds = 120
memory_mib = 6144
result_mib = 256
diagnostic_kib = 16
cpu_threads = 4
nice = 5
```

The parent observes aggregate RSS for the child and all descendants every
100 ms and kills the process group on memory or wall-time excess. The child
also limits CPU, result-file bytes, open descriptors, and core dumps; Linux
adds address/data-space rlimits. A limit breach is a per-file parser failure,
not a worker crash, and cannot activate a worse extraction.

Tune upward only after recording source hash before/after, elapsed time, peak
RSS, extraction status, unit count, and warnings on representative large
documents. Keep at least 2x measured peak RSS and leave enough unified memory
for PostgreSQL, the OS, and OCR. Tune downward by format only through a future
adapter profile; the current profile is intentionally uniform and serial.

This worker is not a permission sandbox. Keep the NAS mount read-only and use
the launch/container network policy for egress denial. Disabling isolation is
appropriate only for deterministic parser development and comparison, not a
production sync. Search and `xlsx-read` never launch these workers.

## PDF backend rollout and rollback

New starter configs use:

```toml
[parsers.pdf]
backend = "pdf_inspector"
tables_enabled = true
```

`pdf-inspector` is installed by the extractor profile and must report version
1.19.0. KIP calls only its `extract_pages_markdown`; the optional OCR that
pdf-inspector ships since 1.15 is not used, and OCR candidates still go to
Kordoc. A PDF that pdf-inspector cannot parse is recorded as failed; there is
no per-document PyMuPDF fallback. Existing extractions keep the previous
pdf-inspector output until re-extracted. Before changing an existing corpus,
including after a `kip update` that raises the pin, prepare a shadow candidate:

```bash
./scripts/kip parser reextract --source SOURCE --extension .pdf
```

Check `parsed`, `rejected`, `failed`, `parser_counts`, and every warning, then
compare page/table counts, OCR reasons, exact locators, source hashes, and the
same golden dataset. Activate only after the measured candidate passes:

```bash
./scripts/kip parser reextract --source SOURCE --extension .pdf --activate
```

Roll back by setting `backend = "pymupdf"`, rerunning the same shadow
extraction, and activating that candidate; do not edit active rows or stamp
projections.

The original public acceptance of pdf-inspector 1.14.2 against PyMuPDF
measured six successful documents, 70 page units, 37 table units, 13 OCR units
on one garbled page, 4.82 s isolated sync versus 15.58 s baseline, and
identical lexical Recall@10/MRR of 1.0000/0.9861.

The 1.19.0 upgrade was measured in a separate migrated PostgreSQL database over
the same six licensed public PDFs, with the 1.14.2 baseline synced and the
candidate activated through `parser reextract --extension .pdf`: 6/6 documents
and 70/70 pages with 0 rejected or failed; table units rose from 37 to 45,
PyMuPDF-fallback table units fell from 3 to 1 and fallback pages from 7 to 4;
OCR candidate pages fell from 1 to 0 and OCR units from 13 to 0 because the
garbled statistics page is now recovered natively as text and Markdown tables.
Lexical Recall@10 100%, MRR 98.6%, nDCG@10 99.0%, and zero unauthorized results
matched the baseline, the top-1 (document, page) hit was identical for 30/30
public relevance cases, p50 latency stayed about 34 ms, and raw parse time was
unchanged at about 0.34 s for all six. Because 1.19.0 emits much more inline
bold and `<sup>` markup, search text (`body_normalized`/`lexical_text` of
pdf-inspector page and table units, and the reranker input for those units)
strips paired Markdown emphasis (`*`, `**`, `***`) and inline presentation tags
(`u`, `sup`, `sub`, `b`, `i`, `em`, `strong`, `s`, `del`, `ins`, `mark`,
`small`); unpaired asterisks such as masked names stay, and other formats are
scored verbatim; without it one case dropped from
rank 1 to 2 (MRR 97.2%). The unit `body` returned by `read` keeps the extractor
Markdown exactly. These are starter/pilot gates, not private-corpus table or
OCR certification.

## PPTX parser validation

Install the pinned extractor extra and include `.pptx` in the intended
filesystem source. Before enabling it for an existing corpus, run unit/contract
fixtures and a read-only sample that contains Korean text, merged tables,
charts, images, groups, notes, hidden slides, and comments. Confirm exact
slide/shape locators, source hash immutability, warnings, and sync/search
behavior. A `partial` result preserves valid slide evidence but requires review
of each `PARTIAL_PARSE`, `SKIPPED_OLE`, or `SKIPPED_MEDIA` warning. KIP does not
execute macros, fetch external links, transcribe media, expand OLE, or parse
legacy `.ppt`.

### Local Korean OCR setup

OCR is enabled in new reference configurations. Bootstrap installs the exact
reviewed runtime and pre-warms its Korean models:

```bash
./scripts/install-kordoc.sh
./scripts/kordoc --version
./scripts/kordoc models --status
```

The version must be exactly `4.13.1`; the `ppocr` group must report `allReady`
and each detector, recognizer, and dictionary file must report `verified`.
The reference configuration resolves the checked-in offline launcher:

```toml
[parsers.ocr]
timeout_seconds = 120

[parsers.ocr.kordoc]
enabled = true
argv = ["kordoc", "--format", "json", "--ocr", "--silent"]
version_argv = ["kordoc", "--version"]
expected_version = "4.13.1"
```

The launcher and production image set `KORDOC_OFFLINE=1` after the verified
cache is present. Do not use `npm`, `npx`, or `@latest` in parser argv. A missing
binary, version drift, timeout, malformed JSON, or OCR failure makes the current
extraction partial while retaining native PDF/PPTX units; it does not replace a
previous active extraction.

Kordoc requires Node.js 20.9+; `doctor.sh` and the installer both refuse an
older runtime. The installer downloads into the ignored versioned runtime
`var/kordoc-4.13.1-r2`, and the production image builds the same isolated npm
root. Both copy `requirements/kordoc/package.json` and its lock and run
`npm ci --omit=dev --ignore-scripts --no-audit`, so the host and the image
install the identical graph: kordoc 4.13.1 with transitive `adm-zip` overridden
to 0.6.0 and `sharp` to 0.35.4. Kordoc binaries and model caches are never part
of the source package ZIP.

`./scripts/audit-kordoc.sh` gates that graph. It first rejects lock/manifest
drift (lock root name, version, dependency and engine blocks, exact pins, and
override versions in every nested copy), then runs
`npm audit --package-lock-only --omit=dev --audit-level=high`. A registry or
network error exits nonzero and fails the gate; it is not a skip. The audit runs
inside `install-kordoc.sh`, in the Docker kordoc stage, in CI (Python 3.12 leg),
in `make audit`, and in `./scripts/verify.sh`, so it needs registry access
during setup, verification, and builds — never during retrieval. The moderate
`adm-zip` advisory GHSA-vwc7-r8mq-g2x9 is still in the graph with no patched
release; `--ignore-scripts` means the ONNX install-time extraction hook that
used it is not executed by the supported CPU installation path, but the advisory
is not removed. See [dependency safety](SECURITY.md#dependency-safety).

Existing deployments are not rewritten. `kip update` reruns bootstrap, which
installs the new runtime, and an `expected_version` of `4.8.0` or `4.7.3`
written by an earlier KIP release resolves to the current pin, so the preserved
`config/kip.toml` needs no version edit; any other non-current value is still
rejected. The PP-OCRv5 Korean cache under `var/kordoc-models/ppocr` is reused
offline and `check-ocr-models` skips files that already exist. To enable OCR
on a deployment that has it off, set `[parsers.ocr.kordoc].enabled = true` in
its local `config/kip.toml` and run `./scripts/doctor.sh` before a shadow
sample.

PPTX defaults accept at most 128 images, 20 MiB per image, 100 MiB total, and
images at least 96x48 pixels. Adjust only after a read-only shadow sample. Check
`ocr_unique_image_count`, `ocr_block_count`, `ocr_skipped_image_count`, exact
page/shape locators, all warnings, and source SHA/mtime before activation.

## Local lexical reranking

In lexical mode, including the lexical fallback of the default semantic mode,
the starter profiles rerank at most 40 ACL-filtered lexical candidates with
candidate-local Okapi BM25. This is local, deterministic, and does not build
embeddings or send document text to a model endpoint. RapidFuzz remains the
supported fallback backend:

```toml
[search]
lexical_rerank_enabled = true
lexical_rerank_candidate_limit = 40

[models.reranker]
enabled = true
backend = "bm25"
max_document_chars = 8000
baseline_weight = 0.15
```

When `[models.reranker]` selects the `http` model backend,
lexical mode and the `semantic_degraded` fallback keep a local reranker from
the optional `[models.lexical_reranker]` table instead of calling the model.
Its keys and defaults match the shipped BM25 settings above, so lexical
results do not change: `backend` (`bm25`, or `rapidfuzz`),
`max_document_chars` (8000), `bm25_k1`/`bm25_b` (1.2/0.75), and
`baseline_weight` (0.15, RapidFuzz only). With a `bm25` or `rapidfuzz`
`[models.reranker]` backend the table is ignored and both paths share that
reranker. `models.reranker.enabled = false` still disables both, and
`search.lexical_rerank_enabled` still decides whether lexical results are
reranked at all.

Query n-grams present in at least `search.lexical_common_term_fraction`
(default 0.02) of lexical units are left out of candidate matching; KIP adds
file and folder names to every unit's lexical text, so a folder name such as
`절차` had matched 55k of 104k units. The BM25 reranker still scores the full
question, and `0` disables the exclusion. The abstention gate uses an
existence check (`any_term_visible`, about 2 ms instead of up to seconds).
On the private reviewed set (19 cases, full 1,912-file corpus, lexical mode)
this moved recall@10 from 78.9% to 89.5%, MRR from 58.3% to 63.8%, nDCG from
63.6% to 70.2%, P50 from 4.40 s to 1.45 s, and P95 from 11.11 s to 2.22 s
(warmed release evaluation).

Candidate documents are reopened through the same ACL- and freshness-aware
repository before reranking. If the adapter is unavailable, KIP preserves the
lexical order and emits `metadata.lexical_rerank_degraded=true`; alert on this
field instead of silently treating the request as reranked. Re-evaluate the
candidate depth, latency, and ranking against each deployment's reviewed
questions before changing these bounds. ADR-031 records the older
source-derived RapidFuzz promotion; ADR-034 records why BM25 superseded it on
the reviewed 19-case set.

## Dependency and model update watch

The daily detector compares the exact `pdf-inspector` and Kordoc pins plus the
embedding/reranker revisions with PyPI, npm, and Hugging Face metadata. An
available version is only a candidate: rerun the matching PDF/parser shadow
gate and preserve an exercised rollback before changing the pin.

Dependabot proposes Python, GitHub Actions, and Docker updates weekly. The
`upstream-watch` workflow runs daily at 09:00 KST and compares
`parsers.ocr.kordoc.expected_version` plus the pinned Hugging Face embedding
and reranker revisions with upstream. It creates or updates one GitHub issue
when drift is detected and closes that issue after every watched pin matches
again. Run `./scripts/check-upstream-updates.sh` for the same read-only check
locally, or dispatch the workflow manually. `scripts/audit-semantic.sh` (run by
`verify.sh`; it needs network access like the Kordoc audit) audits the model
runtime lock `requirements/semantic.txt`; reviewed advisories are listed with
their reasons in `docs/SECURITY.md` and any new advisory fails. Both are discovery surfaces only:
follow `DEPLOYMENT_GUIDE.md` and the quality experiment workflow before
changing a production pin or activating a projection.
