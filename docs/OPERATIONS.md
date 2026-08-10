# Operations

## Daily

```bash
./scripts/doctor.sh
./scripts/kip status
./scripts/kip jobs list --status failed
```

## Incremental sync

Use source-specific cursors. A source outage must not be interpreted as deletion. Files are considered deleted only after a successful complete scan confirms absence according to the configured grace policy.

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
docker compose --env-file /etc/kip/production.env \
  -f compose.production.yaml up -d api worker
```

All three KIP image variables must use the same verified
`repository@sha256:<64 lowercase hex>` reference. Secret paths must be absolute
paths to regular, non-symlink, single-line files with operator-only
permissions. The PostgreSQL password and migration URL use owner credentials;
the API and worker URLs must use separate login roles bound to the NOLOGIN
groups in `deploy/sql/roles.sql.template`. Review and apply that template as
the database object owner after migrations. Never give API or worker
`kip_owner` or `BYPASSRLS`.

The reference API binds only to loopback. Terminate TLS and verify organization
identity in an identity-aware proxy before forwarding the Bearer JWT. The
PostgreSQL network is internal and the NAS bind is read-only. Validate the
resolved Compose model before every rollout and reject a deployment if a
secret, source path, or image digest is still an example value.

## Release artifacts

Run the local gates and build a reproducible handoff bundle from a clean tree:

```bash
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
statement, image lock, required starter files, private-path patterns, and
forbidden secret/data artifacts. A branch or pull-request workflow produces a
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

```bash
export KIP_DATABASE_URL_FILE=/run/secrets/backup-database-url
export KIP_CAS_PATH=/srv/kip/cas
export KIP_BACKUP_PATH=/srv/kip/backups
./scripts/backup.sh
```

The backup set contains:

- PostgreSQL custom-format dump with `row_security=off`;
- canonical JSONL export and database row/migration/extension/RLS manifest;
- CAS archive plus content-hash manifest;
- ontology and redacted configuration snapshot;
- sealed manifest and SHA-256 checksums.

Run backup with the dedicated, audited `kip_backup` login membership or the
database owner. That role needs full read access and verified `BYPASSRLS`;
never reuse API or worker credentials. The script writes a private partial
directory first and atomically publishes it only after every artifact is
sealed. A retained `.partial-*` directory with `FAILED` is incident evidence,
not a usable backup.

The local archive is not encrypted, scheduled, uploaded, or retained by KIP.
Use the platform backup service to encrypt it, copy it off-host, apply legal
retention, and alert on age. Never put a database URL or encryption key in the
archive or repository.

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

## Ontology mining and review

Enable `[models.relation_mining]` only after the generation destination and
egress policy pass the target corpus review. Mining is never triggered by
search or answer requests.

```bash
./scripts/kip ontology entities
./scripts/kip ontology mine --unit-id UNIT_ID
./scripts/worker.sh --once
./scripts/kip ontology candidates --status proposed
./scripts/kip ontology entity-approve ENTITY_CANDIDATE_ID
./scripts/kip review approve RELATION_CANDIDATE_ID
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

Optional OTLP/HTTP export requires the `telemetry` package extra and an explicit
`telemetry.otel.endpoint`. KIP configures batched spans and periodic metrics to
the collector's `/v1/traces` and `/v1/metrics` endpoints. Keep collector
credentials in standard `OTEL_EXPORTER_OTLP_HEADERS` environment configuration,
never in TOML. Telemetry delivery failure is intentionally non-fatal to search,
answering, and mining; use the canonical PostgreSQL trace table to diagnose
collector loss.

### Local semantic shadow

```bash
./scripts/bootstrap-semantic.sh
./scripts/semantic-server.sh run
./scripts/semantic-smoke.sh
./scripts/kip projection rebuild --name semantic
./scripts/kip projection verify --name semantic
```

The isolated semantic environment pins Infinity 0.0.77 and Click 8.1.8.
Click 8.4.x is incompatible with Infinity's Typer 0.12.5 dual boolean flags.
Apple MPS also runs with BetterTransformer disabled because Infinity's optional
Optimum precheck is invalid on that path. Defaults of four embedding inputs and
two reranking pairs per server batch fit the validated 24 GB Apple Silicon
profile; adjust the `KIP_*_SERVER_BATCH_SIZE` variables only after measuring.

`projection verify` accepts a complete `shadow` or `active` space and checks
that its vector count equals current active content units. `projection
activate` refuses an incomplete space. Activation is still a separate operator
decision after `evaluate compare`; the current public pilot says to keep it
disabled.

### Periodic public scorecard

Enable the `public-government` source and both model adapters in the local
`config/kip.toml`, then run:

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

## Parser upgrade

Prepare HWP/HWPX candidates without changing the active index:

```bash
./scripts/kip parser reextract --source company-nas
```

Review `eligible`, `parsed`, `rejected`, `failed`, `unit_count`,
`parser_counts`, and every warning in the versioned JSON envelope. Compare a
reviewed golden corpus and exact evidence before activation. Then run the
separate mutation command:

```bash
./scripts/kip parser reextract --source company-nas --activate
```

The operation scans and hashes only configured HWP/HWPX files. Each candidate
inherits the current canonical source ACL snapshot, scopes, and classification;
parser re-extraction never applies a configuration policy change. Use normal
source synchronization to change ACL policy. Activation then checks the
current artifact revision, source hash, canonical access controls, and minimum
parser quality immediately before the transaction. It inserts a new extraction
and its units, swaps the active lexical rows, and deactivates the previous
extraction in one PostgreSQL transaction. The previous extraction remains
recoverable history. A rejected, failed, changed, stale, or unauthorized
candidate leaves the old active extraction intact. Normal search and
incremental sync never trigger this workflow.

## Local lexical reranking

The starter profiles rerank at most 40 ACL-filtered lexical candidates with
RapidFuzz. This is local, deterministic, and does not build embeddings or send
document text to a model endpoint:

```toml
[search]
lexical_rerank_enabled = true
lexical_rerank_candidate_limit = 40

[models.reranker]
enabled = true
backend = "rapidfuzz"
max_document_chars = 8000
baseline_weight = 0.15
```

Candidate documents are reopened through the same ACL- and freshness-aware
repository before reranking. If the adapter is unavailable, KIP preserves the
lexical order and emits `metadata.lexical_rerank_degraded=true`; alert on this
field instead of silently treating the request as reranked. Re-evaluate the
candidate depth, latency, and ranking against each deployment's reviewed
questions before changing these bounds.

## Dependency and model update watch

Dependabot proposes Python, GitHub Actions, and Docker updates weekly. The
scheduled `upstream-watch` workflow reports newer `kordoc` and Hugging Face
model revisions in one GitHub issue. Both are discovery surfaces only: follow
`STARTER_KIT_GUIDE.md` and the quality experiment workflow before changing a
production pin or activating a projection.
