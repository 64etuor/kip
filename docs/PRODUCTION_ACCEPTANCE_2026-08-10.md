# Production starter-kit acceptance: 2026-08-10

## Verdict

KIP 3.2.0 passes repository and starter-kit acceptance for the canonical
filesystem/PostgreSQL lexical path, exact evidence reads, reviewed ontology
graph context, CLI/REST/MCP parity, hardened distribution, and sealed recovery.
This is not blanket certification of a recipient's private corpus, identity
provider, Slack/mail permissions, OCR, semantic model, or Neo4j deployment.
Those remain environment-specific adoption gates.

## No-context setup acceptance

A fresh project root was driven only through the `kip setup` state machine.
The CLI returned exactly one question per inspection for 15 consecutive
decisions. The source answer contained two separately bounded folders with
different classification and ACL scopes.

- Preview: 3 internal files and 1 confidential file; extension, byte,
  exclusion, symlink, and unreadable counts were explicit.
- Plan: every source mount was read-only; model generation was disabled; the
  missing private evaluation dataset blocked production promotion.
- Apply: generated TOML, Compose, and MCP files were written atomically; prior
  files were retained as `.previous` on reapply.
- Verify: plan fingerprint, source availability, read-only mounts, and the MCP
  generated-config binding all passed.
- Secrets: generated files contained only environment references.
- Source safety: SHA-256 values for every source file were identical before and
  after setup and the complete RAG cycle.

## Live application acceptance

An ephemeral PostgreSQL 18 database received all 13 append-only migrations.
The bundled three-file source then produced:

| Gate | Observed result |
|---|---|
| Dry-run sync | 3 scanned, 3 skipped, 0 failed |
| Incremental sync | 3 scanned, 3 inserted, 0 failed |
| Canonical state | 3 objects, 3 revisions, 3 artifacts, 3 active extractions |
| Search projections | 4 content units and 4 lexical units |
| Korean retrieval | expected memo ranked first; 2 admissible hits |
| Exact read | non-empty body, line locator, indexed/current hash match, not stale |
| XLSX deep read | original `A1:D3`; numeric `C2=1500000`, `C3=890000`; cached values matched |
| Candidate isolation | proposed relation returned 0 graph edges |
| Human approval | one active `belongs_to_project` edge with one evidence unit |
| Ontology answer | one 1-hop path and the approved edge appeared with exact evidence |
| ACL negative | outsider search 0, graph 0, ontology evidence 0, answer refused with 0 citations |
| REST parity | health/search/graph/ontology/answer passed; invalid key returned HTTP 401 |
| MCP parity | stdio initialized, 20 tools discovered, search/graph/answer matched |

The temporary database was dropped after evidence capture. No production or
user database was modified.

## Reviewed quality canary

A two-case immutable canary bound the authorized ontology question and its ACL
denial twin to the actual PostgreSQL corpus and approved assertion. The lexical
report recorded:

- retrieval Recall@10, MRR, nDCG, locator accuracy, and latest accuracy: `1.0`;
- failed cases and unauthorized results: `0`;
- answer claim/citation precision and recall, groundedness, completeness, and
  refusal appropriateness: `1.0`; unsupported claims: `0`;
- ontology entity/relation/evidence precision and recall, path relevance and
  recall, contradiction handling, and temporal accuracy: `1.0`;
- ontology duplicates, orphans, and ACL leakage: `0`.

The zero-result rate was `0.5` by design because one of two cases was the
required ACL denial. This canary demonstrates that the evaluation contracts run
against a real canonical store; it does not replace a recipient's reviewed
30-50 question pilot dataset.

## Distribution and recovery evidence

The release gate builds a clean wheel and non-root image, runs the image with a
read-only root, no capabilities, no-new-privileges, and UID/GID 10001, then
verifies both the starter directory and deterministic archive. Every bundle
contains an image digest lock, SPDX SBOM, SLSA provenance, release manifest, and
SHA-256 checksums; branch artifacts are candidates, while only a matching
`v$(cat VERSION)` tag may publish and attest GHCR subjects.

The recovery drill restored a sealed PostgreSQL/CAS/config backup into an empty
target, verified migrations, extensions, RLS, row counts, and CAS hashes,
rebuilt 45,870 lexical units with no changes or deletions, and reproduced the
fingerprinted retrieval baseline with zero ACL leaks. Local archives still need
external encryption, off-host retention, scheduling, and alerting in each
production environment.
