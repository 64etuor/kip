# Implementation Status

This is the current readiness inventory, not the target architecture. The
target-to-current matrix and ordered gap register live in
`docs/PRODUCTION_DESIGN_ALIGNMENT.md`.

## 2026-09-11 pdf-inspector 1.19.0, Kordoc 4.13.1 and PDF re-extraction (3.11.0)

`pdf-inspector` moves from 1.14.2 to 1.19.0 (ADR-064). `pdf_inspector` stays the
default backend, `pymupdf` the explicit rollback, and PyMuPDF `lines_strict`
still runs only on table pages the inspector flags without a Markdown table; a
PDF the inspector cannot parse is recorded as failed, with no per-document
PyMuPDF fallback. KIP calls only `extract_pages_markdown`, not the optional OCR
pdf-inspector ships since 1.15, so OCR candidates still go to Kordoc. Because
1.19.0 emits far more inline `**bold**` and `<sup>` markup, search text
(`body_normalized`/`lexical_text` of pdf-inspector page and table units, and
the reranker input for those units) now strips paired Markdown emphasis
(`*`, `**`, `***`) and inline presentation tags (u, sup, sub, b, i, em,
strong, s, del, ins, mark, small); unpaired asterisks such as masked names
stay, other formats are scored verbatim, and the `body`
returned by `read` keeps the extractor Markdown exactly. `kip parser reextract`
gains a repeatable `--extension` option (leading dot optional,
case-insensitive; default still `.hwp`/`.hwpx`; an extension without a
registered parser is a validation error; the summary lists `extensions`), so
the documented PDF path is now `parser reextract --source SOURCE --extension
.pdf` in shadow, then the same command with `--activate`. Before this, the
documented PDF re-extraction scanned 0 PDFs. Existing indexes keep 1.14.2
extractions until re-extracted.

A/B evidence in a separate migrated PostgreSQL database over the six licensed
public PDFs (1.14.2 baseline synced, candidate activated through the new
path): 6/6 documents and 70/70 pages with 0 rejected/failed; table units 37 to
45, PyMuPDF-fallback table units 3 to 1 and fallback pages 7 to 4; OCR
candidate pages 1 to 0 and OCR units 13 to 0 because the garbled statistics
page (adoption-reform page 8) is now recovered natively as text and Markdown
tables; lexical Recall@10 100%, MRR 98.6%, nDCG@10 99.0% and zero unauthorized
results, identical to baseline; identical top-1 (document, page) hit for 30/30
public relevance cases; p50 about 34 ms on both; raw parse time unchanged at
about 0.34 s for all six. Without the markup normalization one case fell from
rank 1 to 2 (MRR 97.2%).

Kordoc moves from 4.8.0 to 4.13.1 (runtime root `var/kordoc-4.13.1-r2`;
`adm-zip` 0.6.0 and `sharp` 0.35.4 overrides unchanged; the lock change is
only the kordoc package; the npm advisory set is unchanged and
`--audit-level=high` passes). Example, container and setup-generated configs
write `expected_version = "4.13.1"`; a preserved config still naming `4.8.0` or
`4.7.3` (a pin from an earlier KIP release) resolves to the current pin, so
`kip update` needs no config edit, while any other non-current value is
rejected. The PP-OCRv5 Korean cache is reused offline. The `--format json --ocr
--silent` contract (top-level keys, block keys, warning format) and the HWPX
parse shape (`pageMode: section`) are identical; a synthetic 200-dpi Korean
scanned PDF, a 150-dpi scanned table and an image-only PPTX scored character
similarity 1.0000 on both versions with identical table structure; and a real
`./scripts/kip sync` in the isolated database produced searchable `pdf_ocr` and
`pptx_ocr` units with bbox locators, read through the stdio MCP server.
Upstream 4.9-4.13 changes are mostly HWP/HWPX rendering, generation and PDF
table-grid work; the HWP broker Kordoc path stays disabled by default.

Other updates: MCP Python SDK 2.0.0 to 2.2.0 (a real `./scripts/mcp.sh` stdio
client saw the server version on initialize, 31 tools, `kip.envelope.v1` from
`kip_capabilities`/`kip_search`/`kip_read`, and a KIP `not_found` envelope for
a missing unit), rapidfuzz 3.14.5 to 3.14.6, build constraints setuptools
80.9.0 to 84.0.0 and wheel 0.45.1 to 0.48.0 (with packaging 26.3), and
`docker/setup-buildx-action` v4.3.0. Limits: the PDF evidence covers six public
PDFs only; private-corpus table accuracy, OCR CER/WER on real scans, and
semantic/reranked parity were not re-measured because the model sidecar was
not part of this A/B.

## 2026-09-11 package naming, global launcher and `kip update` (3.10.0)

The distributable is now simply the KIP package: `kip-<version>.zip` with its
`.sha256` sidecar, `KIP-MANIFEST.json` (`kip.package-archive.v1`),
`./scripts/build-package.sh` / `./scripts/verify-package.sh`, the `kip-package`
console script, `scripts/upgrade_package.py`, the `package/` bundle directory
and `docs/DEPLOYMENT_GUIDE.md` (ADR-063). Deployments installed before 3.10.0
still upgrade with one command: the upgrader reads a legacy
`STARTER-KIT-MANIFEST.json`, removes it once the new manifest is in place and
restores it on `--rollback`, the installer falls back to the former asset name
for releases before 3.10.0, the installer upgrades existing deployments with
the upgrader shipped inside the downloaded archive, and every release also
publishes a legacy-format archive (same payload, manifest under its former
name and schema identifier) with its own sidecar for the upgrader shipped with
3.9.x.
The installer additionally writes a `kip` launcher to `~/.local/bin` (or
`--bin-dir`/`KIP_BIN_DIR`) that execs the deployment's `scripts/kip`, and adds
one idempotent `# >>> KIP >>>` … `# <<< KIP <<<` block to the login shell
profile exporting `KIP_HOME` and the launcher directory on `PATH`;
`--no-shell-profile` opts out and bootstrap still never edits a profile. `kip
update` delegates to `scripts/upgrade.sh` (`--version`, `--archive`,
`--dry-run`, `--rollback`, `--no-bootstrap`) and, with `kip version`, runs
without a database. `tests/test_install_and_upgrade.py` covers the launcher and
the idempotent profile block (rerunning replaces rather than duplicates it) and
a legacy-named upgrade with its rollback; `tests/test_cli_surface.py` covers
`update` and `version` without a database. Limits: the profile block is written
only for zsh, bash and POSIX `sh` profiles, so fish and other shells need a
manual `PATH`/`KIP_HOME` entry; the launcher records the deployment path at
install time and follows `KIP_HOME` when the deployment moves or several exist;
and the legacy-named asset copy is a transition aid that can be dropped once no
3.9.x deployment is expected to upgrade directly.

## 2026-09-11 one-command install and in-place upgrade (3.9.0)

An adopter can install a published release with a single command
(`curl -fsSL .../releases/latest/download/install.sh | bash`, default `$KIP_HOME`
or `~/kip`) and update the same deployment in place, either by rerunning that
command on the existing directory or with `./scripts/upgrade.sh
--latest|--version X.Y.Z|--archive ZIP`. The installer is standalone Bash: it
needs only bash, curl or wget, a SHA-256 tool and unzip or python3, verifies the
archive against its `.sha256` sidecar before extracting anything, requires an
absent or empty directory for a fresh install, then runs `./scripts/bootstrap.sh`
and the full `./scripts/verify-package.sh` check. Upgrades apply section 11's
boundary mechanically from the manifest diff: package-owned paths are replaced or
removed, deployment-owned paths are untouched, `.mcp.json` is preserved, and the
previous package files plus `plan.json` are kept under `var/upgrades/<id>/` for
`--rollback`; `--dry-run` prints the plan and the CHANGELOG entries between the
two versions. `tests/test_install_and_upgrade.py` covers sidecar verification,
refusal of a tampered archive with nothing extracted, latest-tag resolution and
refusal of a non-empty target, an in-place upgrade that preserves deployment
state, rollback, refusal of downgrades and git checkouts, a forged archive whose
internal digests disagree, and that both scripts are standalone and executable.
Limits: `curl | bash` trusts GitHub's release hosting and TLS and the sidecar is
served from that same origin, so it detects corruption rather than a compromised
origin (SECURITY); deployments created before 3.9.0 have no `scripts/upgrade.sh`
and take the manual DEPLOYMENT_GUIDE 11.5 procedure once; `--rollback` restores
package files only and does not roll back the database, so a pre-upgrade
`./scripts/backup.sh` is required across a migration boundary; and the tests run
against local file-URL release fixtures with bootstrap skipped, so a real
cold-network install from a published release is not exercised in CI.

## 2026-09-11 review corrections (3.8.2)

An independent review of 3.7.1–3.8.1 found that the named-file fail-closed
guarantee only covered a built-in extension list, so an operator-indexed
extension such as `.png` could still borrow another document's fact; that the
basename resolver scanned every unit name per answer; that URLs were refused
as inaccessible files; and that the Memory and PostgreSQL resolvers differed
on superseded revisions and non-filesystem sources. 3.8.2 corrects each with
regression tests on both backends. First-run experience also changed: an
unreachable database now fails within seconds with a typed
`dependency_unavailable` error and remedy, and an empty search reports
`no_visible_indexed_units` on CLI, REST and MCP. Local probes on this host
confirmed the error path (about nine seconds, no retry noise), the
empty-workspace warning, a pasted-URL question answering normally, and the
full gate. These are correctness and onboarding fixes, not new
retrieval-quality evidence.

## 2026-09-11 prerequisite bootstrap (3.8.1)

Bootstrap can prepare Python and Node/npm locally before loading dotenv; the
missing-Python chicken-and-egg failure is removed. Read-only checks, explicit
Docker installation assistance, bounded readiness and a Docker-free external
database path are available. Compatible existing programs, virtual environments,
profiles and Docker contexts are preserved. A real macOS arm64 cold probe with
Python/Node/uv hidden from PATH installed Python 3.13.15 and Node 22.23.2; a
separate fresh source package bootstrap completed the locked environment and OCR
model checks. Docker native privileged installers are covered by simulated
action-boundary tests, not by removing/reinstalling the user's working Docker.
Windows/WSL Desktop integration, OS authentication, license choices and Linux
daemon permissions can still require user action. See ADR-061 and the deployment
guide; those boundaries are not reported as completed installation.
The first Linux CI cold smoke was blocked because its restricted PATH omitted
GNU tar's gzip helper. 3.8.1 corrects that fixture and checks gzip before Linux
downloads. An Ubuntu 24.04 arm64 container with no Python/Node installed then
completed the real runtime bootstrap and repeat readiness check. The failed
3.8.0 tag was not rewritten and did not publish a release.
The final local full gate passed 1,113 tests, lint/types, Python dependency
auditing and the npm high-severity gate; the four documented moderate adm-zip
propagation findings remain. The portable retrieval/ACL set retained 120/120
cases, recall@k/MRR=1.0 and unauthorized=0. The private corpus floor was not
applicable to this workspace. Local receipts and cold-install logs are under
the ignored `var/audits/prereq-380/` directory.

## 2026-09-10 named-source answer corrections (3.7.1)

The three reproduced 3.7.0 defects are covered by regression checks: named
files retain their scope when ranked below the requested limit or stale;
quoted/backticked names select the same source; and 언제인가/누구인가/무엇인가
no longer become required evidence keywords. The source binding now precedes
retrieval, with inclusion/exclusion on both lexical and vector candidates and
approved ontology evidence. Unknown or inaccessible document references refuse
without revealing out-of-scope existence. Memory/PostgreSQL checks also cover
removed roots, limit=1 exclusion, Unicode casefold ambiguity and edge parity.
See ADR-060 and [agent quality evidence](AGENT_QUALITY.md) for the measured scope.
The final local full gate passed 1,090 tests and retained the 120-case portable
retrieval/ACL results. The unrelated workspace did not run the reviewed private
corpus floor; no broad answer-quality or semantic activation claim is implied.

## 2026-09-10 discovery evidence, retrieval and startup fixes

Search hits now state their own status: `evidence_role` is always `discovery`
and `source_verification` is always `not_checked`, because a hit's hashes
describe the index rather than a live check. Reopened evidence reports how it
was checked — `stat` (size/mtime still matched the indexed revision, no new
digest), `sha256` (live file hashed), or `unavailable` (source unreadable, with
`source_changed_since_index` true) — on `EvidenceRead`, `ContextItem`, and
`AnswerCitation`. `ContextItem.body_truncated` marks a body that is only the
leading portion of the unit. The fields are additive, envelope versions are
unchanged, and generated `contracts/` carry them.

Both Memory and PostgreSQL, lexical and vector, now build the same
paragraph-bounded, query-aware preview (`src/kip/domain/snippets.py`): the
paragraph with the most distinct query terms, source order breaking ties, a
bounded 360-character window, NFC-normalized. PostgreSQL previously returned
the first 500 whitespace-collapsed characters. This is presentation only; it is
not censorship and not exact evidence.

The ACL-scoped vocabulary abstention check now also considers stems of common
Korean particles (의/은/는/이/가/을/를/에/로/와/과/도/에서/으로/에게/까지/
부터/에서는/으로는/에게는) together with NFC normalization. An inferred stem
permits retrieval only if it exists in the visible corpus; ACL checks are
unchanged.

When an entire `answer` query equals the basename of a `file://` source, the
answer returns that document's extracts with citations instead of the
body-relevance refusal. If more than one allowed file with that exact name has
differing content, the answer refuses with `clarification_required`; the check
runs over the ACL-visible corpus before the result limit, so `limit=1` cannot
hide it, and identical copies count as one document. A filename inside a longer
factual question scopes evidence to that document; the rest of the question
must still be present in it or the answer refuses with `answer_not_present`;
a bare name with punctuation or a display verb returns the document, several
named files are a comparison rather than a duplicate, `말고`/`제외` excludes the
named file. As of 3.7.1, approved ontology evidence must respect the explicit
file scope as well (ADR-060).
Shallow XLSX evidence still returns `exact_xlsx_read_required`. Interrogative
endings (언제야, 언제까지야, 누구야, 무엇인지, ...) are no longer subject
keywords and unit titles count toward relevance, so `제출기한은 언제야?` over a
single notice answers instead of refusing, and `A과제 과제번호가 뭐야?` over a
shallow workbook reaches `exact_xlsx_read_required` rather than a generic
refusal. These gates remain lexical heuristics with targeted regressions, not a
measured calibration.

A local probe on the bundled three-file `sample-data` source through both the
CLI and the stdio MCP server on this host confirmed the wire behavior: particle
queries (`정산의`) returned the notice and workbook units with
`evidence_role=discovery`/`source_verification=not_checked`; `정산_안내.txt`
answered with one `stat`-verified citation; `정산_안내.txt 최종 승인일이
언제야?` refused `answer_not_present`; `A과제_정산.xlsx` refused
`exact_xlsx_read_required`; context items reported `stat` and
`body_truncated=false`; `app-up.sh --database-only` and `doctor.sh` passed with
Node 26 and the r2 Kordoc root. The Dockerfile `kordoc` stage was also built
on this host: the in-image audit reported only the four moderate adm-zip
propagation findings and passed at the high threshold, `npm ci --ignore-scripts`
completed, and the version probe and model check succeeded. BuildKit initially
hung resolving the digest-pinned Dockerfile frontend behind the Docker Desktop
proxy until the same image was pulled by tag; `TROUBLESHOOTING.md` records the
workaround. This is a smoke of the shipped contract, not corpus-quality or
external-model evidence.

`./scripts/app-up.sh --database-only` is the guided CLI/MCP startup path. With
a generated deployment it starts only the approved `postgres` service, checks
that `config/kip.host.generated.toml` matches the plan fingerprint and database
secret ref, and runs host `./scripts/migrate.sh`; only the database credential
is required (API/worker/identity credentials are neither read nor required, and
placeholders satisfy Compose interpolation). An external database is migrated
without starting Docker, and no API/worker image is built. Full `app-up.sh` and
`--down` are unchanged, and setup receipts name `--database-only` first in
`next_steps` and `limitations`.

The Kordoc npm graph is locked in `requirements/kordoc/` (kordoc 4.13.1 exact
since 3.11.0, `adm-zip` 0.6.0, `sharp` 0.35.4). The host installer (root
`var/kordoc-4.13.1-r2`) and the Dockerfile stage install it with `npm ci
--omit=dev --ignore-scripts --no-audit`, and `./scripts/audit-kordoc.sh`
rejects lock/manifest drift before running `npm audit --package-lock-only
--omit=dev --audit-level=high`; a registry or network error fails the gate. It
runs in the installer, the image build, CI, `make audit`, and
`./scripts/verify.sh`. Node 20.9+ is required. sharp GHSA-rgj7-g3m4-5g8c is
fixed by 0.35.4.

Remaining limits: the moderate adm-zip advisory GHSA-vwc7-r8mq-g2x9 has no
patched release and stays in the graph — `--ignore-scripts` only stops the
supported CPU installation path from executing the ONNX install-time
extraction hook. Semantic retrieval is unchanged and still opt-in; the Korean
and filename changes are lexical and do not prove general recall. No new
answer-quality evaluation of external generation models has been run, so the
adherence gaps recorded in the audit below are not re-measured.

## 2026-09-10 agent and distribution audit

An independent dispatched follow-up of published 3.6.1 found correct core
results in five fresh MCP sessions without a supplied skill/system prompt,
but two answers added claims without exact reads and four repeated irrelevant
embedded-instruction commentary. OneDrive exact reads and scope denials passed
for two sampled files; a direct answer request refused. A new package
reached generated-config MCP retrieval after isolated database startup, while
full app startup stalled at Dockerfile frontend resolution. Bootstrap exposed
sharp/adm-zip npm advisories outside the Python audit gate. This was not a full
pass; the section above records which of these limitations 3.7.0 addressed and
which remain open. For the audit itself see the detailed
[dispatched evaluation](AGENT_QUALITY.md#independent-dispatched-follow-up-2026-09-10).

Seven controlled native-MCP agent requests were reviewed for tool choice,
exact reads, calculations, missing/stale evidence, and embedded instructions;
see [agent quality](AGENT_QUALITY.md) for outcomes and limits. MCP discovery and
error contracts were hardened, and table completeness now governs answer
admissibility independently of question language (ADR-058). Twelve superseded
implementation checklists were removed. Both distributions now use the same
canonical document allowlist, and verification checks recipient-visible links.
The final local gate passed 951 tests, lint/types, dependency auditing, and the
120-case portable retrieval/ACL gate. The default workspace still had no corpus
for the separate historical private golden floor; this is not private-corpus
answer-quality evidence.

## 2026-09-10 source scope and setup handoff

Enabled filesystem roots now authorize existing indexed evidence before
ranking/limits and graph traversal, with Memory/PostgreSQL parity and migration
0024/0025. Every assertion evidence unit retains current ACL/snapshot/source
checks. Exact reads add live containment/symlink checks. Reloading removed,
disabled, or changed source config hides old records; an explicit sync is
required to establish evidence under changed scope. Cloud placeholders are
deferred before byte reads and skip diagnostics are aggregated (ADR-056).

Setup now selects standalone generated Compose, honors approved host settings
and secret references, uses the installer's non-root UID/GID, and orders DB
readiness/migration before services. Folder shorthand and local/cloud preview
counts reduce initial input; readiness exposes missing secrets, all-cloud
sources, and unprovisioned local generation. Old plans require regeneration
(ADR-057). This is an improved handoff contract, not universal recipient-runtime
acceptance. Target environments must still verify their mounts, identity,
external services, backup, and actual evidence quality.

The bounded OneDrive ingestion sample contained one file per seven supported
office/document formats: 7 inserted, 0 failed, 36 units, and 1,907 cloud-only
files deferred. Total eligible was 1,914; only the selected 302,208-byte sample
was made local, and its size/mtime were unchanged. These counters do not prove
full-corpus retrieval, generated-answer quality, or semantic activation.

OneDrive testing also exposed filenames absent from body vocabulary being
rejected before ranking. The abstention check now considers literal identifiers
within current ACL/root/request filters, including NFC/NFD and literal wildcard
handling, then uses the shared ranking path. Recovered documents required no
reindex. Final bounded probes passed filename search, context, and fresh exact
read for 7/7 files; both XLSX/XLSM `A1:F12` live reads were fresh (2/2), and
direct ACL denials passed for 7/7. Removed, disabled, and narrowed source
configurations each blocked 7 searches, 7 reads, 7 artifact lookups, and 2
workbook reads even with `allow_stale`; context/vocabulary were empty and
answers refused. Repeat sync reported 7 unchanged and 0 failed. These are
sample retrieval/freshness/access results, not generated-answer or semantic
quality acceptance.

An isolated source package installation also completed frozen bootstrap and the
18-question guided path using a directory-only source answer, a distinct
workspace, random local credentials, non-default loopback ports, and the
installer's UID/GID/groups. The generated standalone stack built and started
PostgreSQL, completed migration, and started API/worker. Host CLI ingestion was
visible through authenticated API search and a fresh exact read; API ingestion
was then searchable and freshly readable through the host CLI. Both directions
used the same canonical source path and snapshot. Unauthenticated status
returned 401. The documented REST smoke and teardown wrappers succeeded, and
the temporary services were stopped after testing. This proves this local
deployment path, not arbitrary recipient networks, IdPs, or model providers.

The final local `./scripts/verify.sh` run passed 929 tests, Ruff, mypy, runtime
dependency auditing, and the 120-case portable retrieval/ACL gate. The separate
historical private golden floor skipped because its default workspace was
empty; the OneDrive probes above ran in an isolated audit workspace and are not
substitutes for that full-corpus benchmark.

| Area | Status | Notes |
|---|---|---|
| Root agent files | Ready | Task-routed `AGENTS.md`, Claude import, generated MCP config, and compact portable skills with conditional references (ADR-055). Invalid explicit runtime paths fail closed; installation stages both bundles and rolls back handled failures |
| Canonical contracts | Ready | Pydantic models and generated JSON Schema |
| Online source package ZIP | Ready | Deterministic single-root ZIP of allowlisted source, locked inputs, tests, contracts, migrations, ontology, examples, automation, and canonical operating documents. A strict versioned manifest, per-file checksums, external archive digest, path/size/symlink defenses, required-file checks, and private/secret scan are enforced by both build and verify commands. Local state, internal plans, private evaluation data, databases, CAS/output data, and release binaries are excluded; this source handoff does not replace signed production provenance |
| Production distribution | Ready | Digest-pinned non-root image, read-only Compose profile (with the one deliberate exception of the `${KIP_ONTOLOGY_PATH}` bind mounted read-write into the API for discovery auto-release, ADR-044), locked runtime/build inputs with tested core/runtime parity, wheel, image lock, SPDX SBOM, SLSA provenance, deterministic archive, private-data scan, and directory/archive verifier. The parser supervisor's `psutil` dependency is present in the production runtime lock; both that lock and the installed all-extra environment pass `pip-audit`. A clean core-wheel install now starts `kip capabilities` without optional extractors because PPTX image transcoding loads Pillow only on demand |
| CI supply-chain gates | Ready | Current-release SHA-pinned actions, Python 3.12/3.13 matrix, contracts, architecture, Ruff, mypy, dependency audit, migrations, tests, 75% coverage, clean-wheel smoke, hardened-image smoke, candidate bundle, and tag-only GHCR publish with attestations. A structural test rejects workflow extras that are not declared by `pyproject.toml`, preventing the removed Neo4j extra from breaking installs again. Local `verify.sh` preflights pytest, Ruff, mypy, and pip-audit and fails if any is absent; uv uses the frozen lock, while pip-only installations use project-interpreter modules |
| Backup and recovery | Ready for operational adoption | Sealed PostgreSQL/CAS/config backup, `row_security=off` manifest, explicit empty-target restore, row/migration/extension/RLS/CAS comparison, projection rebuild, fingerprinted evaluation comparison, and checksummed drill receipt. `--retain N` pruning (default 7), a redacted configuration snapshot with a seal-and-verify secret rescan, and a launchd daily schedule (`com.kip.backup`; the installer supports `--dry-run`) are included; a real sealed set was produced and checksum-verified on this host on 2026-08-13 (`var/backups/20260813T070625Z`) |
| Host operations reporting | Ready for pilot | `scripts/ops-report.sh` summarizes failed jobs, oldest queue age, last sync progress age, disk free, newest backup age, and API health (`/readyz` with `/healthz` fallback) with tunable thresholds, `--json`, an optional `KIP_OPS_WEBHOOK` failure POST, and an optional launchd item; `install-launchd.sh` also guards against a double worker and renders a newsyslog rotation policy |
| Memory repository | Ready | Used for tests and offline smoke checks |
| PostgreSQL migrations | Ready as the production reference profile | Workspace, required-scope, ACL-snapshot freshness, and owner-bound `FORCE ROW LEVEL SECURITY` are included. Normal migration installs pgvector, the 1024d projection, and migration 0018's cosine HNSW index; semantic activation remains separate |
| PostgreSQL repository | Pilot reference | Core ingest, search, exact read, ACL, job, assertion, export, and rebuild methods implemented; evidence and graph reads are ACL- and freshness-prefiltered; repository calls share a bounded connection pool (`database.pool_max_size`). Re-syncing unchanged files no longer fails: configuration-owned ACL-snapshot timestamps refresh while snapshot identity fields stay strictly verified (previously every second `kip sync run` raised a per-file ConflictError) |
| CLI | Ready for pilot | JSON-first commands; source-neutral `sync run`, top-level `xlsx-read`, projection and canonical export aliases; operator roles come only from explicit `--role`/`--roles`/`KIP_ROLES` and admin commands fail closed. Search exposes the full canonical request including mode and filters; explicit ACL options replace ambient scopes |
| REST API | Ready for pilot | Read, exact evidence, assertion explain, connector event, sync, and review endpoints, including the versioned candidate listing, assertion revocation, and a `/readyz` readiness endpoint that round-trips the database (`/healthz` stays liveness-only); trusted API-key or verified JWT identity; blocking handlers run synchronously in the server threadpool instead of on the event loop |
| Python SDK | Ready as a thin REST client | Capabilities, search, context, answer, evidence, graph, ontology, jobs, and review helpers delegate to REST. Search, context, and answer expose the canonical mode and filter set while omitted defaults stay absent from payloads |
| Identity and ACL snapshots | Ready for pilot | JWT issuer/audience/JWKS verification, configured API-key principal, stale dynamic snapshot exclusion, and legacy identity-header rejection |
| Data classification and model egress | Ready for pilot | Canonical source/unit classification, local loopback policy, remote provider/classification/retention/secret gates, and atomic denial decisions |
| Structured generation adapters | Ready for pilot | Provider-neutral typed contract with bounded HTTP responses, explicit timeouts, pinned model revisions, request IDs, token accounting, citation-ID validation, and OpenAI Responses/Anthropic Messages adapters. Claims support a `disputed` certainty that must cite every disagreeing evidence unit, and the prompt instructs the model to surface source conflicts instead of picking one |
| MCP | Ready for pilot | Optional stdio adapter shares the application services and now uses the stable MCP 2.0 `MCPServer` API (ADR-051), reports the KIP package version, negotiates the current protocol with legacy-client support, and preserves `kip.envelope.v1`. Guided setup points `.mcp.json` at the host-path `config/kip.host.generated.toml`; in-process MCP 2 client coverage plus real stdio discovery/tool-call QA protect the edge. Streamable HTTP and server-initiated backchannels remain outside the shipped adapter |
| Filesystem connector | Ready for pilot | Read-only traversal with an mtime settle window; content hashes are lazy and unchanged files are skipped by size/mtime against the stored revision without being read. Deletion is reconciled by a complete-scan grace policy (migration 0020, `[sync] deletion_grace_scans` default 2): filtered, oversize, symlink-policy, and settle-window files are counted as present; directory walk errors fail the scan; failed and empty scans never contribute deletion evidence; genuinely absent files are soft-tombstoned and reappearances re-index. The sync summary reports `absent` and `tombstoned` counts. Sentinel/count-drop guards and a descriptor-pinned hash/parse path remain unimplemented |
| Parser process isolation | Ready for NAS pilot | Every filesystem parser is composed behind the unchanged `ParserPort` and runs one document per fresh child (ADR-050). The M4 Pro 24 GB reference profile is serial with 4 native threads, 6 GiB aggregate process-tree RSS, 120 CPU seconds, 180 wall seconds, a 256 MiB file response, bounded diagnostics, process-group teardown, descriptor/output/core limits, and nice 5. Contract tests cover timeout descendants, real RSS excess, output caps, typed failures, and raw/isolated equivalence. An independent scoped Luna rerun used 10 locally allocated anonymized OneDrive samples across all seven configured formats: raw and isolated contracts matched, status counts were 8 succeeded/2 partial/0 failed on both paths, every source hash stayed unchanged, and 94 focused tests plus synthetic resource/cleanup probes passed. This rerun peaked at 155 MiB process-tree RSS; an earlier exercised sample remains the conservative observed maximum at about 1.15 GB. macOS uses parent RSS supervision; Linux adds address/data-space rlimits. Read-only source and network denial remain outer deployment controls; OCR semantic accuracy, XLSX deep semantics, placeholders, and full-corpus quality remain outside this scoped PASS |
| Text/CSV parsers | Ready for pilot | A leading comment line no longer defeats delimiter sniffing (previously every row collapsed into one field at quality 1.0 with no warning), and control-byte binary that decodes as valid UTF-8 is now flagged `BINARY_SUSPECTED` with reduced quality instead of passing as clean text. Plain text/Markdown decode through a bounded encoding ladder (BOM strip, UTF-8 strict, CP949 strict, then visible degraded fallback with replacement-ratio warnings and content-derived quality — CP949 Korean exports no longer index silently as mojibake). `.csv` routes to a structural parser: sniffed delimiter, header-column metadata, row-boundary chunking, `csv_rows` start/end-row locators, ragged rows warn without failing; numeric CSV values are indexed verbatim (unlike XLSX shallow) |
| DOCX parser | Ready for pilot | Footnote/endnote text is extracted as dedicated units (it was silently dropped at full reported quality until 2026-08-16), `w:noBreakHyphen` no longer glues words together, tracked deletions stay excluded while insertions stay included, and field codes yield their cached display text — each locked by regression tests. Symbol-font glyphs (`w:sym`) remain a known drop (needs a per-font mapping). Structural single-pass XML walk (measured 2026-08-15 rebuild): paragraph-range chunk locators (`docx_paragraphs`), dedicated table units (gridSpan/vMerge without duplication), header/footer parts, hyperlink targets and heading levels in metadata, text boxes extracted once as `docx_textbox` units (mc:Choice/Fallback dedup), image counts, per-part partial isolation, and content-derived quality. Previously a single flat whole-document unit that dropped headers/footers and duplicated text boxes; re-extract existing DOCX to benefit |
| XLSX shallow/deep | Ready for pilot | Hidden sheets are indexed but now flagged (`hidden` per unit, `hidden_sheet_count` per extraction); shared formulas, error cells, and 1904-date-system workbooks are verified correct by regression tests; named ranges remain unextracted. Quality is content-derived (sheet success ratio × replacement penalty; a corrupt sheet degrades instead of aborting). Shared-string shallow index and exact-shape `.xlsx`/`.xlsm` range reader; JSON-safe scalar/cached values, normal/array/data-table formulas, Excel serials/formats, merged cells, and hidden/filtered dimensions are explicit. Date/datetime/time use ISO 8601, durations use ISO 8601 duration strings, non-finite numerics stay labeled rather than becoming null, and dense validated ranges are capped at 100,000 cells |
| PDF parser | Ready for starter/pilot | The default `pdf_inspector` 1.19.0 backend emits structured per-page Markdown, table/column signals, and per-page OCR reasons; valid Markdown tables become additive `pdf_table` units and detected table pages without valid Markdown use selective PyMuPDF `lines_strict` fallback (ADR-054). `pymupdf` remains the explicit rollback backend. Separate migrated PostgreSQL A/B databases over six public PDFs produced 6/6 successful syncs, 70 pages on both sides, 37 candidate tables versus 44 baseline tables, and 13 OCR units on one garbled page; raw parsing was 19.2x faster and isolated sync 3.23x faster while lexical Recall@10/MRR remained 1.0000/0.9861 with zero ACL leaks. Explicit reranked evaluation remained unavailable because the embedding sidecar was not running. The 1.19.0 upgrade (ADR-064), re-extracted and activated with `parser reextract --extension .pdf` in a separate migrated database, kept 6/6 documents and 70/70 pages with 0 rejected/failed, raised table units from 37 to 45 (PyMuPDF-fallback tables 3 to 1), recovered the garbled page natively (OCR units 13 to 0), and kept lexical Recall@10/MRR/nDCG@10 100%/98.6%/99.0%, zero unauthorized results and 30/30 identical top-1 (document, page) hits; search text and reranker input strip paired inline Markdown emphasis and presentation tags for pdf-inspector units while `read` keeps the extractor Markdown. A pdf-inspector failure fails that file, with no per-document PyMuPDF fallback. Kordoc 4.13.1 PP-OCRv5 Korean remains the offline candidate enrichment path with audited npm overrides. Private table accuracy, OCR CER/WER, semantic/reranked parity, memory peaks, encrypted PDFs, and full-corpus quality still require deployment-specific shadow evidence |
| PPTX parser | Ready for retrieval pilot | Grouped-shape geometry is now converted to slide-absolute coordinates (a group moved after grouping previously reported raw local coordinates while claiming `coordinate_space: slide_emu`, corrupting position and reading order); ancestor rotation is deliberately not folded into the bbox. `python-pptx` plus bounded OOXML scan and default Korean picture OCR in new reference installs; text, merged tables, sparse chart caches, image metadata/hash, nested groups, notes, legacy comments, SmartArt text, hidden slides, geometry, source z-order, and derived reading order are structured. A read-only SolarEdge `5_PROJECT` run parsed 55/55 PPTX files into 77,922 JSON-valid native units with zero source-stat changes. OCR QA added 28 located units from seven images in `GEN2 적용 예시.pptx` and 124 from thirteen images in `FAT문제점 및 차트들.pptx`, with unchanged sources and explicit low-confidence warnings. No legacy `.ppt`, media transcription, modern threaded comments, or OLE expansion. Quality is content-derived (part-failure ratio × replacement penalty), and `kip doctor` verifies Kordoc resolvability when OCR is enabled |
| HWP broker | Ready for retrieval pilot | Native HWP/HWPX signatures and 86/86 real-file extraction are validated; guarded shadow/atomic activation preserves prior extractions. Parser 1.1 chunks with a 400-char overlap so boundary-spanning facts stay retrievable (86/86 re-extracted and activated). Kordoc-compatible command output preserves structured table/image/span/footnote/list/link metadata and warning page context. Evidence units now carry a verified `section` index (ADR-049: native per-section reconstruction checked byte-for-byte against the library's own `extract_text()`, falling back to `section: null` with a warning rather than guessing; the label is numeric even though the dependency orders section files lexically). Page locators remain impossible on this format and paragraph/table locators remain incomplete; quality now uses the shared hangul/printable content formula instead of a flat constant |
| Slack connector | Reference adapter | Threads are ingested as one semantic event keyed on the root message (replies become revisions); validate scopes, rate limits, edits/deletes, and retention |
| Apple Mail connector | Reference adapter | macOS permission and mailbox allowlist required |
| IMAP connector | Reference adapter | Validate provider-specific UID behavior |
| Public evaluation corpus | Ready | Six checksum-pinned KOGL Type 1 PDFs; 30 relevance and 6 ACL cases |
| Evaluation reports | Ready with coverage gaps | Retrieval, answer, and ontology metrics; immutable dataset/review binding; full-case coverage gates; ACL/integrity checks; fingerprints; Markdown scorecards; append-only ledger; search hits now carry `is_latest` and the runner reopens evidence for stale-warning measurement, so both dimensions are measurable; judge-proposed dataset growth (ADR-045: `kip evaluate draft validate/review/promote` with fingerprint-bound human sample-audit and fail-closed promotion) unblocks growing reviewed sets past the current 19 cases; public locator/recovery and end-to-end reviews remain incomplete |
| Retrieval regression gate | Active in hosted CI and private runners | The checked-in portable manifest expands to 100 positive and 20 ACL-negative cases and always runs in CI/verify. `scripts/golden_gate.py` checks the reviewed private floor; protected runners set `KIP_REQUIRE_PRIVATE_GOLDEN=1` so missing corpus evidence fails closed |
| Quality control plane | Ready for pilot | Version-pinned parser/embedding/reranker/retrieval experiment manifests and fail-closed, read-only promotion recommendations; manifest-driven orchestration is not yet a scheduler |
| End-to-end RAG rubric | Ready for pilot | Deterministic claim/citation/refusal and entity/relation/evidence/contradiction/path/temporal/integrity metrics; missing reviews fail closed and the bundled ontology case is synthetic contract evidence only |
| Query tracing and metrics | Ready for pilot | PostgreSQL/RLS canonical redacted traces, admin-only CLI/REST inspection, bounded retention pruning, non-fatal delivery, and optional OTLP/HTTP spans and metrics without content attributes |
| Adaptive ontology and interaction memory | Ready for pilot | Empty starter profile, one-question setup selection (including an explicit, generation-provider-gated `relation_mining_mode` decision), TTL owner-scoped clarifications, confirmed preferences, structured non-trace feedback, per-principal discovery candidates, PostgreSQL RLS, and CLI/REST/MCP parity. Generated host/container configs carry the selected bounded relation-mining table. Low-risk `review: not_required` mined relations gain a measured auto-approve lane (ADR-047, opt-in/default-off after ADR-048: precision >= 0.95 over >= 20 human decisions, confidence >= 0.8, fail-closed, tamper-resistant via a dedicated `auto_approved` column and revocation-aware, reported and revocable); conditional/required predicates stay fully human, and ontology mutation requires the admin role on every surface. Admin approval of an entity-type or predicate discovery candidate materializes an additive ontology release automatically (ADR-044): shadow-validated, comment-preserving, idempotent targeted YAML edits with a minor version bump and review-policy sync; auto-released predicates default to `review: required`/`risk: high`. Long-running API/worker/MCP processes report `catalog_refresh: "restart_required"` and pick up the release on restart; each CLI invocation sees it immediately. The shipped configurations enable `interaction.enabled` and `ontology.adaptive_discovery` by default |
| Evidence-bounded answer | Ready for bounded pilot; broad quality gate pending | CLI/API/MCP/SDK share search, exact reopen, freshness, XLSX, egress, generation validation, citations, extractive fallback, and approved-graph context. Identifier, numeric, focused-fact, and short multi-document adequacy gates return typed `answer_not_present` or `clarification_required`; broader reviewed answer/citation/refusal coverage remains required |
| Local embedding sidecar | Runtime validated; current shadow rebuild required | Infinity 0.0.77, Qwen3 0.6B 1024d, pinned revisions, and MPS smoke passed; resumable projection uses current active ACL-fresh units and versioned bounded input. The 2026-08-13 `c4000` private space completed 30,565/30,565 with vector Recall@10/MRR 0.947/0.822, P95 133.75 ms, and zero ACL leaks. Current code/reference config uses a distinct 12,000-character `c12000` identity, which has not been rebuilt or evaluated, and stale-warning evidence is absent. The historical report cannot activate the current configuration (`evaluation/reports/semantic-qwen3-all-modes-final-20260813/decision.md`) |
| Local reranker | BM25 active; RapidFuzz fallback; model adapters shadow | RapidFuzz (pinned 3.14.6; the gate ran on 3.14.5) reranks bounded ACL-filtered lexical candidates locally and passed the private OneDrive retrieval gate; BGE/Jina model adapters remain opt-in shadow candidates. A candidate-local Okapi BM25 backend (`models.reranker.backend = "bm25"`, word+bigram Korean tokens, no model or extension dependency) beat RapidFuzz on the 19-case grounded draft set (Recall@10 0.842 vs 0.684, MRR 0.639 vs 0.566, lower P95; see `evaluation/reports/reranker-ab-20260811/decision.md`) and was promoted on 2026-08-11 after the dataset was adversarially re-verified and versioned (`reviewed 1.0.0`, ADR-034); RapidFuzz remains the fallback backend |
| Search result diversity | Active | Per-document cap (`search.max_hits_per_document`, default 3) with tail backfill across every search path, so one file cannot occupy all result slots |
| pgvector and HNSW | Production-profile ready; current semantic shadow absent | PostgreSQL 18/pgvector 0.8.2, RLS/source-hash filtering, and migration 0018 HNSW with bounded strict iterative scan are implemented; EXPLAIN confirmed the index path. The complete 30,565/30,565 private Qwen3 space used the former `c4000` identity. Current `c12000` configuration requires a new rebuild and evaluation before activation |
| Hybrid retrieval | Implementation complete; current shadow unverified | ACL-prefiltered exact vector search, RRF, bounded reranking that preserves the un-reranked fused tail up to the request limit, and explicit activation are implemented. On the historical reviewed 19-case `c4000` run, vector-only Recall@10/MRR was 0.947/0.822, ahead of hybrid at 0.895/0.702 and reranked at 0.842/0.656; the current `c12000` identity has no matching report |
| Alias query expansion | Active for the lexical path | Human-approved entity aliases (ACL-prefiltered `resolve_entities`) expand candidate retrieval only; reranking keeps the user's original wording. It lifted RapidFuzz on the grounded draft set and is aggregate-neutral-to-positive under the now-active BM25 backend; re-evaluate if candidate generation changes (`evaluation/reports/alias-expansion-20260811/decision.md`) |
| Ontology contract | Ready for pilot | YAML entity inheritance and predicate contracts; collision-safe validation (ADR-043: a domain profile redefining a core entity type or predicate, or a `sources/*.yaml` object type with an unknown parent, fails `kip ontology validate` and container startup); ACL-bound mining jobs; strict structured-output validation; reviewed entities/relations; exact evidence; deterministic fingerprints; current approved-graph answers; and idempotent predicate migration materialization with source-assertion lineage. The curation loop is reviewable end to end (ADR-038): approved-entity-aware mining digests make the two-pass mine -> approve entities -> re-mine loop run, invalid/duplicate/stale proposals are skipped with per-proposal reasons on a durable `kip.ontology-mining-result.v1` job payload, evidence/review enforcement is derived from the catalog and pinned to `predicates.yaml` by a contract test, candidate listings ship as triage-ordered `kip.assertion-candidate-listing.v1` with Korean labels and ACL-gated snippets, audited revocation and supersede-on-approve exist (migration 0019), and `include_candidate_assertions` populates clearly-marked proposed candidates on ontology-context surfaces only |
| Agent-guided setup | Handoff implemented; recipient acceptance required | Folder shorthand, metadata-only local/cloud preview, resolved secret/key readiness, owner-bound plans, standalone generated Compose, generated host-config selection, and app-up-first receipts are implemented (ADR-057). Missing readiness does not become `verified`. Local generation provisioning, external controls, and real recipient evidence remain separate |
| Neo4j | Not shipped | Graph traversal runs inside the active repository backend (`capabilities.graph_backend` reports it); a Neo4j read projection would introduce its own port at adoption time. Do not deploy before the adoption gate |
| Review UI | Not included | CLI/API review workflow only |
| package adoption guide | Ready | Environment decisions, AI change contract, real-corpus acceptance evidence, upgrade and handoff rules |
| Upstream update watch | Ready for GitHub-hosted repositories | Dependabot covers Python/Actions/Docker; a behavior-tested daily workflow reads the OCR `expected_version`, reports Kordoc and Hugging Face revision drift in one GitHub issue, and closes it when pins match again without activating an update |

## Explicit pilot limitations

- Backup archives are deliberately not encrypted or uploaded off-host by KIP.
  Local daily scheduling (`com.kip.backup`) and `--retain` pruning now exist on
  the macOS host, but production must still provide an external secret manager,
  an encrypted off-host backup policy, an identity-aware TLS edge, and push
  alerting; the only built-in alert path is the optional `ops-report.sh`
  failure webhook (`KIP_OPS_WEBHOOK`).
- Tag publishing requires repository permissions for GHCR packages, OIDC, and
  GitHub attestations. A locally verified candidate does not prove that the
  repository's tag-only publication path has run.
- `compose.production.yaml` is a hardened reference, not an orchestrator or
  secret manager. It requires `${KIP_NAS_PATH}` bound read-only into both the
  worker and the API (the API opens the live source tree for evidence
  freshness and `xlsx-read`), targets `/readyz` for the API healthcheck, and
  probes worker database connectivity; operators must still supply non-owner
  database login URLs, regular secret files, immutable image digests, storage,
  TLS/IAP, and deployment-level rollback, dashboards, and paging.
- The default sync mode is incremental. HWP/HWPX has an explicit non-mutating
  `parser reextract` shadow command and a separate guarded `--activate` action;
  other registered formats such as PDF are selected with `--extension`.
  Generic all-format forced re-extraction and destructive source
  reconciliation are intentionally not exposed as one-step starter commands.
- Filesystem deletion reconciliation is complete-scan-only and fail-safe: a
  failed, permission-incomplete, or aborted scan marks nothing, and a scan that
  sees zero files is skipped with a warning. Present files deferred by settle,
  symlink, filter, or size policy keep their prior active revision and never
  contribute absence evidence. A truly emptied source tree is therefore not
  tombstoned automatically. The TRD's additional sentinel-file, count-drop,
  and permission-ratio mount guards beyond the empty-scan skip are not
  implemented.
- The unchanged-file optimization trusts matching size and mtime before
  hashing. Production read-only mounts make ordinary source mutation an outer
  control, but a descriptor-pinned check/hash/parse chain and provider-specific
  cloud-placeholder handling are not implemented. Treat a same-stat rewrite or
  hydration race as a pilot limitation and force a reviewed re-extraction when
  it is suspected.
- The `sync_schedule` setup answer is declarative operational metadata only;
  nothing schedules syncs automatically. Periodic execution comes from
  `install-launchd.sh` (which uses its own interval setting) or an external
  scheduler.
- The PostgreSQL integration test is gated by `KIP_TEST_POSTGRES_URL`; CI or a local PostgreSQL service must run it before deployment.
- Pgvector/HNSW is part of the supported PostgreSQL production reference
  profile. A future extension-free distribution would need a separate migration
  and CI matrix; disabling semantic search does not uninstall the extension.
- Canonical search mode and filters now have CLI/REST/MCP/SDK parity, and
  capabilities prove a compatible complete active space rather than
  configuration alone. Current CLI exit statuses still group most typed KIP
  errors under code 3, and list/search edges do not expose cursor pagination.
- The portable 120-case gate blocks deterministic stage/filter/ACL regressions
  in hosted CI. It does not replace the reviewed private corpus; only a protected
  runner with `KIP_REQUIRE_PRIVATE_GOLDEN=1` supports the stronger merge claim.
- Context-free private-corpus QA still shows candidate-recall limits: some
  supplier paraphrases and Korean-English code-switched queries rank relevant
  evidence near or outside a small lexical result window. Conservative
  identifier, numeric, focused-fact, and ambiguity gates now refuse instead of
  presenting unsupported extractive success, but the behavior needs a larger
  reviewed end-to-end set.
- Explicit local CLI ACL values replace ambient `KIP_ACL_SCOPES`, including an
  explicitly empty set. Repository/RLS and local outsider probes observed no
  leak; production identity still comes from the configured adapter.
- Multi-user production requires an identity-aware proxy that issues the
  configured JWT claims. KIP verifies those claims directly and rejects legacy
  caller identity/ACL headers; target-provider revocation latency is bounded by
  the shorter of token and ACL-snapshot expiry.
- Remote model use requires an approved provider contract and classification
  allowlist. The `zero_retention` field is configuration evidence, not
  independent verification of the provider account setting.
- The bundled remote generators disable ambient proxy discovery and require an
  explicit endpoint, immutable model revision, and environment-backed secret
  reference. `keychain:` and `secret-manager:` references are rejected at
  setup answer time with guidance; only `env:` references resolve everywhere,
  `file:` resolves only for the model credential, and the database URL and
  bootstrap identity keys accept `env:` alone.
- Optional HWP parser commands, Slack scopes, Apple Mail Automation permissions, and IMAP provider behavior must be validated against the target environment.
- PPTX structural extraction and default Kordoc 4.13.1/PP-OCRv5 Korean image
  OCR are local and non-executing in new reference installs. The runtime and
  model cache are installed and verified before offline indexing. Audio/video transcription,
  embedded OLE/package expansion, modern threaded comments, formula OCR, and
  legacy binary `.ppt` remain unsupported.
- Neo4j is not shipped (no port or adapter scaffolding); graph traversal is a
  repository capability and canonical assertions are queried from PostgreSQL.
  A Neo4j read projection would be introduced behind a new port at adoption
  time.
- The current public pilot is small and lexically distinctive. Its
  `keep_disabled` semantic decision must not be generalized to a private corpus
  without reviewed internal golden cases. The reviewed private OneDrive set now
  shows a material vector gain, including semantic-paraphrase Recall@10
  `0.429 -> 0.857`, with zero exact-recall regression and zero ACL leaks.
  The historical `c4000` HNSW run preserved those metrics at P95 `133.75 ms`,
  below the `2000 ms` gate. Current code uses the distinct `c12000` identity,
  so that projection must be rebuilt and re-evaluated. Semantic projection
  also remains disabled because stale-warning coverage is absent and fails
  closed.
  Separately, the 2026-08-10 native-HWP OneDrive A/B first promoted local
  RapidFuzz on a 253-query source-derived set; ADR-034 superseded that default
  with candidate-local BM25 after the reviewed 19-case comparison.
- The 2026-08-06 loaded-corpus audit is recorded in `docs/RAG_QUALITY_AUDIT_2026-08-06.md`; lexical remains active and all semantic candidates remain shadow-only.
- Quality recommendations do not discover, install, or activate libraries. Candidate dependencies remain opt-in adapters; a scheduler may automate shadow runs only after reproducible manifest execution is added.
- Retrieval-only reports cannot claim end-to-end RAG quality. Promotion requires
  immutable reviewed claim/citation/refusal and ontology observations for every
  case in each declared dimension; the bundled ontology starter is synthetic
  contract evidence, not a private-corpus quality result.
- Predicate rename, replace, split, merge, and deprecate manifests are validated
  before ACL-filtered source assertions are materialized as target-version
  review candidates. Existing assertions remain active until a separate review
  decision. Entity-type migrations with live entities fail closed until an
  identity-history workflow can preserve merge and split semantics.
- Relation mining is opt-in and reuses the configured generation adapter and
  central egress policy. Jobs pin the submitter access snapshot, fail if it
  expires before processing, and never auto-promote entity or relation output.
- Ontology answer context resolves normalized canonical names and aliases from
  the repository index, traverses bounded current approved paths, and reopens
  every assertion evidence unit. It does not provide historical as-of queries;
  expired and future assertions are intentionally absent from normal answers.
- Query trace delivery is deliberately non-blocking. Production monitoring must
  alert on collector gaps and run the retention prune schedule; PostgreSQL
  remains the incident-review source when OTLP delivery is unavailable.
- Interaction memory is intentionally not a self-training system. It stores no
  raw feedback text, does not read during normal retrieval, and never promotes a
  discovery candidate. Operators must schedule expired-clarification pruning and
  use a non-owner API/worker role before multi-user deployment.

## Self-improvement canary: 2026-08-06

The current 18,496-unit PostgreSQL corpus was re-evaluated after adding the
quality control plane. Run `eval_20260806T103708498279Z` used the pinned
36-case public-government dataset and the new code fingerprint
`sha256:e928d33c685e3503a51eeb360953744abe686869046d3ef7637a6a1a1b8660f6`.
Lexical retrieval retained Recall@10 `1.000`, MRR `0.9347`, nDCG@10 `0.9501`,
zero failed cases, and zero ACL leaks; P95 was `630.77 ms`. Locator,
latest-version, stale-warning, and final-answer dimensions remain unmeasured,
so this run is a retrieval canary rather than end-to-end RAG certification.
Artifacts are under `evaluation/reports/self-improving-rag-20260806/`.

The pinned BGE reranker audit report was also processed through the new
recommendation command. It returned `keep_disabled`: Recall did not improve,
P95 was `10029.13 ms` against a `2000 ms` ceiling, and required evidence
metrics were unmeasured. No projection or model activation changed.

## OneDrive native parser and local reranker: 2026-08-10

An isolated PostgreSQL A/B parsed 86/86 real HWP/HWPX files into 263 native
units with zero failures and unchanged source hashes. Across 253 source-derived
queries, PostgreSQL lexical Recall@1/Recall@5/MRR was
`0.9407/0.9881/0.9596`; bounded RapidFuzz reranking reached
`0.9684/0.9960/0.9796` and added `15.072 ms` at P95. RapidFuzz is therefore the
winner for this **historical source-derived experiment**, not the current
starter default. ADR-034 later promoted candidate-local BM25 on the reviewed
19-case set; RapidFuzz remains its fallback. Kiwi, deterministic proximity, and
the Kiwi ensemble remain rejected. The 253 queries were not reviewed
natural-language answer or ontology cases; those dimensions remain explicitly
unmeasured. See
`evaluation/reports/onedrive-hwp-native-rapidfuzz-20260810/decision.json` and
ADR-031.

## Operations hardening evidence: 2026-08-13

A real sealed backup set was produced on this host with retention pruning
enabled: `var/backups/20260813T070625Z` (453 MiB), all `SHA256SUMS` entries
verified OK, and the configuration snapshot passed the seal-and-verify secret
redaction rescan. A live `ops-report.sh` run on the same host flagged the
data volume at 99% used — below the 10% free-disk threshold — which is a real
operator warning, not a tooling defect: free disk space before relying on the
daily backup schedule. Semantic search remains shadow/disabled, Slack and
mail connectors remain disabled reference adapters, and the active reranked
lexical pipeline retains its multi-second golden-gate P95 (about 7.4 s on the
2026-08-13 reviewed 19-case run), so retrieval latency is an open quality
item, not a regression introduced by these operations changes.
