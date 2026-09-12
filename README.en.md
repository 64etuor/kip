# KIP Knowledge Fabric

**KIP indexes company documents scattered across NAS, HWP/HWPX, PDF, PPTX,
XLSX, Slack and mail, and returns the exact source locator with every answer.**
The Korean front door is [`README.md`](README.md).

```bash
curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash
cd ~/kip && ./scripts/app-up.sh --database-only
kip sync run --source sample
kip search "참여율 변경 승인" --limit 1
```

A real response from that run against the bundled `sample-data/`, abridged to
the fields below with ids and hashes shortened. [`README.md`](README.md) shows
the full envelope.

```json
{
  "schema_version": "kip.envelope.v1",
  "ok": true,
  "data": [
    {
      "unit_id": "unit_5551af4ab2b0…",
      "title": "A과제_메모.md",
      "snippet": "2026년 6월 15일 참여연구원 구성 변경으로 참여율 조정이 필요하다는 논의가 있었다. …",
      "score": 3.662907300901048,
      "locator": { "type": "text_line_range", "data": { "end_line": 5, "start_line": 1 } },
      "source_uri": "file:///.../sample-data/A%E1%84%80%E1%85%AA%E1%84%8C%E1%85%A6_%E1%84%86%E1%85%A6%E1%84%86%E1%85%A9.md",
      "source_sha256": "79770d17e005…",
      "evidence_role": "discovery"
    }
  ],
  "error": null,
  "meta": { "request_id": "req_ebc61f0c81a9…", "workspace": "default", "warnings": [] }
}
```

Snippets are discovery aids: reopen each cited unit with `kip read`, and read
spreadsheet numbers with `kip xlsx-read`. Judge freshness from
`source_verification` first: `unavailable` means the source could not be read,
`source_changed_since_index` is `null`, and the unit is unverified, neither
current nor changed. The full rule is in
[`skills/knowledge-fabric/references/evidence.md`](skills/knowledge-fabric/references/evidence.md).

CLI, REST/OpenAPI and the optional stdio MCP adapter share one application
service layer, so authorization and behaviour match across all three.
PostgreSQL 18 with pgvector owns canonical state; search, vector and graph
projections are rebuildable. Default search is `hybrid` (lexical + vector
reciprocal-rank fusion, ADR-065) and degrades to lexical with a
`semantic_degraded` warning when the model runtime or projection is not ready.

- Install and first index: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- Terms: [`docs/GLOSSARY.md`](docs/GLOSSARY.md)
- Day-to-day operations: [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- Deploy and upgrade: [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)
- Readiness against approved goals:
  [`docs/PRODUCTION_DESIGN_ALIGNMENT.md`](docs/PRODUCTION_DESIGN_ALIGNMENT.md)
- Retrieval evidence: [`docs/RAG_EVALUATION.md`](docs/RAG_EVALUATION.md)
- Security: [`docs/SECURITY.md`](docs/SECURITY.md) ·
  Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md) ·
  Licence: MIT, see [`LICENSE`](LICENSE) and
  [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) (two copyleft components)
