# Quickstart

처음 복제하거나 제3자 환경에 적용한다면 명령을 실행하기 전에
[`STARTER_KIT_GUIDE.md`](STARTER_KIT_GUIDE.md)의 데이터 경계, ACL, 외부
전송, 품질 기준 결정을 먼저 완료한다.

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

For a real read-only OneDrive audit, use [`docs/AI_OPERATOR_RUNBOOK.md`](AI_OPERATOR_RUNBOOK.md); it defines parser comparison, source-grounded validation, A/B scoring, and the post-fix cycle.

## Application profile

```bash
docker compose --profile app up -d --build
curl http://127.0.0.1:8080/healthz
```

The API and CLI call the same service layer. App integrations should use REST/OpenAPI unless the calling system specifically supports MCP.

Push a change from a custom application connector:

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H "Content-Type: application/json" \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  -H "X-KIP-Workspace: default" \
  -H "X-KIP-ACL-Scopes: workspace:default,project:A" \
  --data-binary @examples/connector/event.json
```

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
