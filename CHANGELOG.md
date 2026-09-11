# Changelog

## 3.12.0 - 2026-09-12

- Semantic search is on by default (ADR-065). New configs and
  setup-generated configs enable `search.semantic_enabled`, the pinned
  Qwen3-Embedding-0.6B space (1024 dimensions, 4000-character head/tail
  projection) and the default mode `hybrid` (lexical and vector candidates
  fused by reciprocal rank). The BM25 reranker stays on for lexical mode and
  the lexical fallback; the BGE reranker-v2-m3 cross-encoder is opt-in
  (`models.reranker.backend = "http"`, `KIP_SEMANTIC_RERANKER=on`), because on
  the reviewed private set it did not improve ranking and tripled latency.
  Enabling it no longer moves lexical mode and the lexical fallback onto the
  cross-encoder: they keep BM25 through the new optional
  `[models.lexical_reranker]` table (defaults equal the shipped BM25 settings),
  and reranked hits record `metadata.rerank_model`.
  Bootstrap installs the isolated model runtime from the hash-locked
  `requirements/semantic.txt`, prefetches the embedding model (about 1.2 GB;
  about 2.3 GB more with the reranker) and never fails on it;
  `KIP_SEMANTIC=off` keeps a lexical-only install, and machines below 8 GiB
  of RAM skip it. `app-up.sh` starts exactly one runtime per machine,
  `install-launchd.sh` supervises it (`com.kip.semantic`),
  `deploy/systemd/kip-semantic.service` covers Linux hosts, and compose adds a
  digest-pinned, embedding-only `models` service (profile `semantic`) that API
  and worker reach through the new `security.model_service_hosts` allowlist
  without enabling remote model egress (production: offline on an internal
  network, both models fetched once with the `models-fetch` profile).
- Every sync and activated re-extraction embeds new or changed units and
  reports `semantic_projection` in its summary; a complete projection whose
  identity the release reviewed activates automatically
  (`search.semantic_auto_activate`), while other models still need
  evaluation and `kip projection activate`. `kip projection rebuild` pages
  through pending units, bounds each request by characters as well as count,
  and auto-activates the reviewed space. CLI syncs print embedding progress.
- Search degrades instead of failing: `semantic_degraded` when the runtime or
  projection is unavailable, and the fused lexical+vector ranking with
  `rerank_degraded` when only the reranker fails; both now appear in envelope
  `meta.warnings`. A circuit breaker skips a failed runtime for
  `models.circuit_cooldown_seconds`, and query embeddings have their own short
  timeout.
- Lexical search on large corpora: query n-grams found in at least
  `search.lexical_common_term_fraction` of units (file and folder names are
  indexed into every unit) no longer drive candidate matching, and the
  abstention check stops at the first visible match. On the reviewed private
  set this raised lexical recall@10 from 78.9% to 89.5% and MRR from 58.3% to
  63.8% while P95 fell from 11.1 s to 2.2 s. Projection queries evaluate ACL
  and source policy once per file instead of once per unit, and projection
  statements use `database.projection_statement_timeout_ms` (default 5
  minutes) instead of the interactive statement timeout.
- Model runtime memory: GPU inference runs in float16 (embedding cosine to
  float32 ≥ 0.99998), the Metal allocator cache is capped, and the default
  document cap is 4000 characters; at 12000 the runtime reached a 15 GB
  footprint on a 24 GB Mac; the default embedding-only runtime uses about
  2.7 GB. `semantic-server.sh stop` waits for the process and then
  force-stops it instead of leaving a busy server running. One runtime runs
  per machine: `start` skips and `run` (launchd/systemd) waits without
  loading models while any runtime runs, answers or holds the port, and
  `app-up.sh` does not start the compose `models` service while this
  checkout's runtime runs or loads or another runtime answers. `start` and
  `wait` with `KIP_SEMANTIC_RERANKER=on` refuse an instance that lacks the
  reranker, model probes time out after 5 s, and `uninstall-launchd.sh` also
  removes `com.kip.semantic`. Compose PostgreSQL gets tunable memory
  settings (`shared_buffers` 1GB by default instead of 128MB).
- `kip capabilities` reports semantic readiness from the active space's
  identity without counting the corpus; `kip doctor` and
  `kip projection verify` run the full completeness check and report a stale
  active space.
- Upgrading: `kip update` keeps each deployment's config, so an existing
  lexical config stays lexical. To adopt the default, install the runtime
  (`./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch`),
  then run `kip setup plan` and apply it again (a plan from an earlier release
  re-applies as lexical-only), or edit `search.semantic_enabled` and
  `[models.embedding]` by hand; the next sync embeds and activates the
  reviewed space. A semantic config without `search.default_mode` now uses
  `hybrid`; configs from earlier releases that set
  `default_mode = "reranked"` keep it until changed. An inline REST sync
  (`enqueue=false`) now includes that embedding, so prefer the queued sync
  for large sources.
- Quality gates: `./scripts/golden-gate.sh` replaces the documented Python
  invocations, which did not run. The portable gate also runs the shipped
  default mode with a deterministic embedding, and the private gate checks
  per-variant floors for the default mode and the lexical fallback; it fails
  when the deployment's configured default mode has no floor or the floor
  file is malformed or non-numeric. On the reviewed private set (19 cases,
  176,545 units) recall@10 / MRR / P95 were:
  lexical 89.5% / 63.8% / 2.2 s, vector 89.5% / 83.3% / 0.08 s, hybrid
  89.5% / 89.5% / 2.4 s, BM25-reranked 78.9% / 59.8% / 2.3 s and
  BGE-reranked (20 candidates) 94.7% / 85.3% / 7.2 s, with no unauthorized
  results.
  `scripts/audit-semantic.sh` audits the runtime lock in `verify.sh`, with
  reviewed advisories documented in `docs/SECURITY.md`.

## 3.11.0 - 2026-09-11

- Upgrade `pdf-inspector` 1.14.2 -> 1.19.0 (ADR-064). On the six public PDFs
  in a separate migrated database: 6/6 documents and 70/70 pages, table units
  37 -> 45, PyMuPDF fallback pages 7 -> 4, and the garbled statistics page that
  needed OCR is now recovered natively with its tables; lexical Recall@10,
  MRR and nDCG (100% / 98.6% / 99.0%) and the top-1 page for 30/30 cases are
  unchanged, with zero unauthorized results. Existing indexes keep their
  current extractions until re-extracted (see below).
- Search text no longer contains presentation markup. pdf-inspector 1.19.0
  marks bold, italic and superscripts inline (`**시범사업**을`), which split
  Korean words for the n-gram index and the BM25 reranker. For pdf-inspector
  page and table units, search text and reranker input now drop paired
  Markdown emphasis (`*`, `**`, `***`) and inline presentation tags; unpaired
  asterisks such as masked `홍**동` stay, other formats are scored verbatim,
  and `read` still returns the extractor's Markdown unchanged.
- `kip parser reextract --extension .pdf` (repeatable) re-extracts PDFs (or
  any registered format) as a shadow candidate and, with `--activate`,
  replaces them atomically. The default remains `.hwp`/`.hwpx`; unknown
  suffixes are rejected, a run that finds no files of the requested formats
  warns, and the summary lists `extensions`. The operations
  guide previously pointed PDF upgrades at a command that scanned no PDFs.
  After updating, run it once per PDF source to adopt the new parser.
- Upgrade Kordoc 4.8.0 -> 4.13.1 (lock change limited to `kordoc`; advisory
  set unchanged). OCR output on Korean scans, table scans and image-only PPTX
  is identical to 4.8.0. A preserved deployment config that still pins
  `expected_version = "4.8.0"` (or `"4.7.3"`) now resolves to the current pin,
  so `kip update` needs no manual config edit; other values are still refused.
- Dependencies: MCP Python SDK 2.2.0 (verified against a real stdio server),
  rapidfuzz 3.14.6, build constraints setuptools 84.0.0 and wheel 0.48.0
  (with packaging 26.3; the hash file now includes setuptools, which the
  Dependabot proposal had dropped), and `docker/setup-buildx-action` v4.3.0.

## 3.10.0 - 2026-09-11

- Retire the "starter kit" name. The release archive is `kip-<version>.zip`
  with `kip-<version>.zip.sha256`, its manifest is `KIP-MANIFEST.json`
  (`schema_version` `kip.package-archive.v1`), the build/verify commands are
  `./scripts/build-package.sh`, `./scripts/verify-package.sh` and the
  `kip-package` console script, the source modules are `kip.package_archive*`,
  the in-place upgrader is `scripts/upgrade_package.py`, the release bundle's
  source directory is `package/`, and the adoption guide is
  `docs/DEPLOYMENT_GUIDE.md`. Compatibility: the upgrader and the model still
  read `STARTER-KIT-MANIFEST.json` and `kip.starter-archive.v1` from
  deployments installed before 3.10.0 (the legacy manifest file is removed on
  upgrade and restored by rollback), the installer falls back to the former
  asset name for releases before 3.10.0, and each release also publishes a
  legacy-format `kip-starter-kit-<version>.zip` (+ sidecar) built by
  `scripts/legacy_archive.py`: the same payload with the manifest under its
  former name and schema identifier, which is what the 3.9.x upgrader
  requires, so `kip update` from 3.9.x keeps working with one command. When
  the installer upgrades an existing deployment it applies the archive with
  the upgrader shipped inside that archive, so a 3.9.x deployment can also be
  upgraded by re-running the one-line installer; the deployment's new
  `upgrade.sh --finish` then runs bootstrap, migrate and doctor.
- Make `kip` a global command. The installer writes a launcher to
  `~/.local/bin/kip` (`--bin-dir` to change) that runs the deployment's own
  `scripts/kip`, and adds a marked, idempotent block to the login shell
  profile (`~/.zshrc`, `~/.bashrc`/`~/.bash_profile`, or `~/.profile`)
  exporting `KIP_HOME` and putting the launcher directory on `PATH`;
  `--no-shell-profile` skips the profile change. The block is replaced, never
  duplicated, and nothing else in the profile is touched. Paths are
  shell-quoted, a symlinked profile is rewritten through the link with its
  mode preserved, a new macOS `~/.bash_profile` sources an existing
  `~/.profile`, a foreign `~/.local/bin/kip` is backed up to `kip.bak`, and
  `--dry-run` leaves launcher and profile untouched. `scripts/kip` (and so the
  launcher) now exits 69 with `KIP runtime is not installed at …; run
  scripts/bootstrap.sh` when the deployment has no `.venv` and the system
  Python cannot import the CLI, instead of a Python traceback.
- Add `kip update` (`--version X.Y.Z`, `--archive ZIP`, `--dry-run`,
  `--rollback [--rollback-id ID]`, `--no-bootstrap`), which runs
  `scripts/upgrade.sh` and streams its output, and `kip version`. `--rollback`
  has no preview, so combining it with `--dry-run`, `--archive` or `--version`
  is rejected by both the CLI and `upgrade.sh` instead of rolling back.
  Rolling back an upgrade that renamed the manifest also removes the newer
  `KIP-MANIFEST.json` so the restored legacy manifest is authoritative again. Both work without a database; git checkouts
  are still refused with the `git pull` hint.

## 3.9.0 - 2026-09-11

- Add a one-command installer published with every release:
  `curl -fsSL https://github.com/64etuor/kip/releases/latest/download/install.sh | bash`.
  It needs only bash, curl or wget, a SHA-256 tool and unzip or python3,
  downloads the versioned starter kit with its `.sha256` sidecar, refuses to
  extract on any digest mismatch, installs into an empty directory and runs
  `./scripts/bootstrap.sh` (passing `--check`, `--install-docker`,
  `--without-docker`). `--version X.Y.Z` pins a release; `--no-bootstrap`
  extracts only.
- Add in-place upgrades for kit-based deployments: `./scripts/upgrade.sh
  --latest|--version X|--archive ZIP`, also reached by running the installer
  on an existing directory. The manifest diff decides what changes: files in
  the installed or new `STARTER-KIT-MANIFEST.json` are replaced or removed,
  everything else (`.env`, `config/kip*.toml`, `compose.generated.yaml`,
  `var/`, `secrets/`, ontology and golden additions) is untouched, and
  `.mcp.json` is preserved. Previous kit files and the plan are kept under
  `var/upgrades/<id>/` for `--rollback`; `--dry-run` prints the plan and the
  CHANGELOG entries between the versions. Downgrades, git checkouts and
  archives whose digests do not match are refused. Bootstrap, migrate and
  doctor run afterwards.

## 3.8.2 - 2026-09-11

- Fail closed for every indexed extension: a question naming a file whose
  extension is in the deployment's `include_extensions` refuses when no
  allowed evidence matches, not only for the built-in document list. URLs in
  a question are context, not file requests, and the refusal names the file it
  could not resolve. A question that only excludes a file asks for the actual
  question instead of searching the excluded text.
- Resolve named files with an exact casefolded basename lookup on spellings
  extracted from the question (quoted spans, or an extension token with up
  to nine preceding words) backed by migration 0026's expression index and
  0027's artifact join index, falling back to containment only when a
  file-looking token matched nothing. Parentheses and brackets inside or
  after a name (`회의록(최종).txt`, `[공지] 안내.txt`) are recognized. The
  Memory adapter now mirrors PostgreSQL: current revisions and filesystem
  sources only.
- Report an unreachable PostgreSQL within seconds as a typed
  `dependency_unavailable` error that names the target and the
  `./scripts/app-up.sh --database-only` remedy, without connection retry noise.
- Return `no_visible_indexed_units` in `meta.warnings` when a search or
  context request finds nothing and no indexed unit is visible to the caller,
  identically on CLI, REST and MCP; it never implies that hidden units exist.
- Bootstrap: apply the managed runtime PATH once across nested wrappers,
  explain a failed uv or managed-Python probe instead of exiting silently,
  and re-check the npm version after installing the Node bundle.
- Documentation: current bootstrap description in OPERATIONS, one
  `app-up.sh --database-only` path in the guides and `make up`, corrected
  search envelope shape and onboarding steps in README, and the managed Python
  integrity delegation noted in SECURITY.

## 3.8.1 - 2026-09-11

- Include GNU tar's gzip dependency in the cold-runtime test environment and
  check it before a Linux bootstrap download. The first 3.8.0 release attempt
  was correctly blocked by that Linux CI failure; 3.8.1 includes the correction.

## 3.8.0 - 2026-09-11

- Bootstrap without a preinstalled Python: prepare checksum-pinned uv and a
  project-local managed Python when needed, then load dotenv and create the
  virtual environment. Reuse compatible existing Python/Node installations;
  download a verified local Node/npm bundle when missing or too old.
- Add read-only prerequisite checks, explicit Docker installation assistance,
  bounded engine readiness checks and an external-database path without Docker.
  Preserve existing environments, Docker contexts, shell profiles and container
  packages. Native administrator/license steps remain visible user actions.
- Share managed runtime discovery across wrappers and exercise a real cold
  Python/Node bootstrap in CI. Guided external-database readiness no longer
  incorrectly requires Docker for the CLI/MCP path.

## 3.7.1 - 2026-09-10

- Bind named-file answers to the caller's allowed indexed filenames before
  ranking, result limits and live freshness checks. Missing, inaccessible or
  stale named evidence no longer falls back to another file's facts. Apply
  inclusion/exclusion to both lexical and vector candidates and ontology evidence.
- Share filename parsing across scope, ambiguity and answer preparation,
  including balanced quotes/backticks, particles, exclusions and overlapping
  names. Keep Unicode caseless matching consistent across repository backends.
- Normalize interrogative words before stripping noun particles, so
  언제인가/누구인가/무엇인가 do not cause false evidence refusals.

## 3.7.0 - 2026-09-10

- Add a database-only startup path for CLI/MCP users: wait for the approved
  PostgreSQL service and migrate without building API/worker images or requiring
  their credentials. External databases are migrated without starting Docker.
- Share a locked Kordoc npm graph between bootstrap and Docker, update sharp
  to 0.35.4, disable dependency lifecycle scripts, and enforce the high-severity
  npm audit in installation, builds, CI and full verification. Node 20.9+ is
  required. The remaining adm-zip advisory stays visible; its ONNX installation
  hook is not executed by the supported CPU installation path.
- Return paragraph-bounded, query-aware previews consistently across lexical
  and vector backends. Search results explicitly identify unverified discovery;
  reads, context items and citations expose their actual verification method,
  and context items disclose truncation.
- Preserve Korean subject retrieval across common grammatical particles and
  Unicode normalization without bypassing ACL checks. The vocabulary
  abstention check resolves all candidate terms in one PostgreSQL round trip.
  Preview window scoring is bounded per distinct query term and capped at 64
  terms, so long repetitive bodies cannot make search previews expensive.
- Answer requests that name a file: a bare exact filename, also with trailing
  punctuation, a particle, or a display verb (`계약서.pdf?`, `계약서.pdf 보여줘`),
  returns that document's cited extracts. Same-named files with differing
  content are detected across the ACL-visible corpus before the result limit
  and ask for clarification; identical copies count as one document. A factual
  question that names a file is scoped to it and refuses as
  `answer_not_present` when the asked fact is not there; naming two different
  files is a comparison, `A.txt 말고 ...` (an exclusion marker right after
  the name) excludes that file while negations elsewhere in the question stay
  content, an embedded mention needs a file extension, approved ontology
  evidence is retained, and incomplete tables still refuse.
- Stop interrogative endings such as 언제야/언제까지야 from counting as subject
  keywords and let unit titles count toward question relevance, so a
  one-topic question is not refused by its own evidence and a workbook named
  in the question reaches the exact-read refusal instead of a generic one.
- Derive the Kordoc runtime root and expected version from
  `requirements/kordoc/package.json` in the launcher, installer, and doctor
  (`scripts/kordoc-runtime.sh`), so a manifest bump cannot leave the runtime
  wrapper pointing at a stale installation.
- Make the private golden gate skip, or fail closed under
  `KIP_REQUIRE_PRIVATE_GOLDEN=1`, when none of the reviewed set's expected
  documents are indexed in the workspace, instead of reporting a false
  zero-recall regression over an unrelated corpus such as the bundled samples.
  Partial coverage is printed before the metrics.
- Record independent agent/OneDrive/cold-install outcomes and open evidence,
  startup, and npm dependency limitations. Correct the setup guide to test the
  approved source from its receipt rather than assuming a sample source exists.

## 3.6.1 - 2026-09-10

- Describe every MCP tool's purpose and mutation effects, advertise canonical
  request bounds, and preserve the versioned envelope for domain validation
  and unexpected handler failures. Discovery approval now accurately describes
  its immediate YAML release and restart implications.
- Treat shallow workbook units as discovery, independent of question language.
  Refusals include exact-read locators; complete evidence can still answer when
  a shallow workbook also matched. Partial CSV evidence requires contiguous
  full-row coverage that fits the context budget. Excluded evidence cannot
  leave an uncitable ontology context in the answer.
- Clarify missing currency and formula-cache semantics; record seven native
  MCP agent cases covering Korean/English questions, workbook calculation,
  changed decisions, missing/stale evidence, and embedded hostile instructions.
- Remove 12 superseded implementation checklists and align both distribution
  formats on the canonical document allowlist. Historical links point to pinned
  Git history. Verification checks Markdown links against the shipped payload.

## 3.6.0 - 2026-09-10

- Enforce the currently configured filesystem sources as a deployment access
  boundary before retrieval ranking, limits, vocabulary, and graph traversal.
  Removing, disabling, or narrowing a source blocks its old indexed evidence
  after configuration reload without an implicit sync. Exact reads also check
  the current root and symlink policy. Source-root moves cannot reuse an old
  path's cached revision, and CLI workspace selection now precedes composition.
- Defer OneDrive/cloud-only files before hashing or parsing; report aggregate
  skip reasons instead of per-file warning floods. Directory symlink escapes
  and cycles are pruned before descent, with conservative deletion deferral.
  Special filesystem items such as FIFOs are skipped before hashing.
- Make guided setup runnable across the host CLI and standalone generated
  Compose deployment. Approved settings survive bootstrap defaults, all app
  services share the selected database, and only approved source mounts are
  present. Existing generated deployments must create, review, and apply a new
  plan before using the new launcher.
- Accept directory paths directly during source setup, show locally available
  versus cloud-only document counts, validate secret readiness, and generate
  random private credentials only for a new bootstrap environment. Runtime
  ownership is recorded in the plan so private files stay readable by the
  selected non-root container user. Setup now starts dependencies before sync.
- Generated host and container source paths now use the same canonical absolute
  path, so CLI and API/worker share source identity and can reopen each other's
  ingested evidence. Protected runtime mount collisions are rejected. The REST
  smoke wrapper uses the approved port, secret reference, and interpreter;
  teardown can stop the stack even when credential files are unavailable.
- Added real OneDrive and isolated setup acceptance probes alongside expanded
  Memory/PostgreSQL/CLI/REST/MCP permission and handoff regression coverage.
- Restore literal filename discovery when body vocabulary has no matching
  terms, without re-indexing or weakening abstention for unknown queries.
  Filename/identifier checks retain all access filters and support NFC/NFD
  Korean names and literal wildcard characters.
- Bootstrap now installs the frozen lock with all supported runtime/test
  extras. A missing uv uses the CI-pinned version in a separate tool environment
  rather than resolving newer runtime libraries. Missing configured database
  secrets fail with a versioned CLI error instead of using transient storage.

## 3.5.1 - 2026-09-10

- Reduced always-loaded agent instructions to task routing and operational
  traps; command help, schemas, and canonical docs remain the detailed sources.
  Retrieval skills now prefer connected MCP, keep consent and exact-evidence
  boundaries, and load mining/interaction details only when needed. Corrected
  stale discovery-release guidance and unsupported date-filter advice.
- An invalid explicit `KIP_PROJECT_DIR` now fails instead of silently querying
  another runtime. Skill installation stages both bundles before replacement
  and restores the previous installation on handled copy, replacement, or
  pointer-update failures; symlink bundles are rejected.
- Full verification now requires pytest, Ruff, mypy, and pip-audit before
  running checks. The pip-only path uses the project interpreter's modules;
  the uv path uses `--frozen`. Missing tooling can no longer produce a passing
  release gate.

- The tagged release step is now idempotent. It previously always ran
  `gh release create`, so re-running a tag failed against the release a prior
  attempt had left behind — and deleting a tag demotes its release to a draft,
  which then stays invisible. The step now edits, publishes, and re-uploads
  with `--clobber` when a release for the tag exists, and creates one
  otherwise.

## 3.5.0 - 2026-09-09

- Tagged releases now publish a GitHub release carrying the starter kit ZIP
  and its `.sha256`, the release tarball, the wheel, and the SBOM, provenance,
  and manifest JSON. Previously the tag-gated publish job pushed the container
  image but left the release payload as a workflow artifact that expires and
  requires a signed-in user to locate the run, so an adopter had no durable way
  to obtain the starter kit. The starter archive is also built, verified, and
  covered by the release provenance attestation in the same job.

- Fixed the distribution CI job, which could never succeed: it asked buildx
  for `load: true` together with provenance and SBOM attestations, and
  attestations make buildx emit a manifest list that the docker exporter
  cannot export. The candidate image is now built without attestations,
  which is all the hardened smoke test and the bundle digest need. The
  published image is unaffected; the tag-gated publish job still builds with
  `provenance: mode=max` and `sbom: true` and pushes to a registry.

- Upgraded the pinned `httpx2` transitive dependency of the MCP 2.x SDK from
  2.10.0 to 2.12.0, clearing CVE-2026-84379, CVE-2026-84380, and
  CVE-2026-84382 in the runtime lock. `httpcore2` moves to 2.12.0 with it.
- `./scripts/verify.sh` now runs the same `pip-audit` over
  `requirements/runtime.txt` that CI runs. The gate previously omitted it, so
  a runtime lock carrying a known advisory could pass local verification and
  fail only after a release tag was pushed.

- Added `THIRD-PARTY-NOTICES.md` and a README license section. KIP is MIT, but
  the pinned runtime installs AGPL-3.0 PyMuPDF into the production image and the
  default PDF path calls it for table fallback, and psycopg is LGPL-3.0-only.
  Neither was disclosed anywhere in the repository. The notices file records
  every declared dependency with its license and names the opt-out for a
  PyMuPDF-free deployment; the SPDX SBOM from `make release` stays authoritative
  per release.

- Starter archive manifests now record the source `repository` alongside
  `git_commit`, so an adopter who received only a ZIP can find where the kit
  came from. The value is normalized to scheme, host, and path: an access token
  carried in the remote's userinfo and a local clone path are dropped rather
  than shipped, and a non-http(s) remote records `null`. `kip-starter-kit build
  --repository URL` overrides detection. Existing manifests without the field
  remain valid `kip.starter-archive.v1` documents.
- Documented the starter-kit upgrade boundary: which paths a new kit replaces,
  which belong to the deployment and must survive, and the one file that is in
  both sets. Overwriting a deployment with a newer kit previously reverted the
  guided-setup MCP configuration with no error.

## 3.4.0 - 2026-08-17

- Added the local `pdf-inspector` 1.14.2 hybrid PDF backend and made it the
  starter default, retaining explicit `pymupdf` rollback. Page Markdown and
  per-page OCR reasons come from the bounded Rust engine; valid Markdown tables
  become `pdf_table` units, while detected table pages without structured
  Markdown use selective PyMuPDF `lines_strict` fallback. On six public PDFs,
  raw parsing was 19.2x faster and isolated PostgreSQL sync 3.23x faster while
  lexical Recall@10/MRR stayed 1.0000/0.9861 with zero ACL leaks (ADR-054).
- Upgraded the pinned offline Kordoc runtime from 4.7.3 to 4.8.0 after a
  compatibility gate over six public PDFs and two public HWPX files preserved
  blocks, page counts, metadata, and warning codes. KIP continues to consume
  the established block contract and ignores the additive per-page Markdown
  projection. Corrected Kordoc `pageMode="layout"` to map to KIP's `exact`
  evidence locator instead of being mislabeled `section_approx`. The online
  installer and production image enforce patched `adm-zip` 0.6.0 and `sharp`
  0.35.3 overrides in an isolated runtime after the published default graph
  exposed high-severity advisories; the overridden graph audits clean
  (ADR-053).
- Added a deterministic, allowlisted online source starter ZIP with a versioned
  manifest, per-file checksums, an external archive digest, path and expansion
  defenses, private/secret-content scanning, build/verify CLI commands, and
  standalone shell entry points. The ZIP carries only source, locked inputs,
  tests, contracts, migrations, ontology, examples, automation, and canonical
  operating documents; local state, internal plans, private evaluation data,
  databases, CAS/output data, generated package metadata, and release binaries
  are excluded (ADR-052).
- Upgraded the optional MCP edge from the 1.x maintenance line to the stable
  2.0 SDK. The adapter now uses `MCPServer`, reports the KIP package version
  during initialization, negotiates the current protocol while retaining the
  SDK's legacy-client support, and preserves `kip.envelope.v1` tool payloads.
  Added an MCP 2 client discovery/tool-call contract test.
- Refreshed the locked Python toolchain and runtime dependencies, including
  mypy 2.3.1, PyMuPDF 1.28.2, Uvicorn 0.52.3, Ruff 0.16.3, and their transitive
  locks; regenerated the production runtime requirements.
- Fixed hosted CI after the Neo4j extra was removed: the workflow no longer
  requests the nonexistent extra, and a structural test now rejects any CI
  extra not declared by `pyproject.toml`. Refreshed SHA-pinned GitHub Actions
  to their current releases. Local `verify.sh` now runs Ruff and mypy through
  the locked uv environment instead of silently skipping installed tools that
  are absent from the shell `PATH`.
- Guided setup now asks explicitly whether ontology entity/relation mining
  should be enabled. The choice is fingerprinted in the versioned setup plan
  and written to `[models.relation_mining]` in both generated configs;
  enabling it with a disabled generation provider fails at the answer boundary.
- Filesystem deletion reconciliation now treats files deferred by the settle
  window or symlink policy as present, matching the existing filtered/oversize
  protection, so a live NAS file cannot age into a false tombstone. Directory
  walk errors now fail the scan before absence reconciliation instead of being
  mistaken for a complete scan.
- Regenerated the production runtime lock so the parser supervisor's `psutil`
  dependency is installed in container images, added a parity regression test,
  and raised the opt-in semantic Transformers floor from the vulnerable 4.57
  line to `>=5.5.4,<6` (currently locked at 5.15.0). Runtime and full installed
  dependency audits report no known vulnerabilities.
- Fixed the base wheel startup contract: PPTX OCR now imports Pillow only when
  it must transcode a non-pass-through image, so a clean core-wheel install can
  run `kip capabilities` without the optional extractor extra. Production and
  parser deployments still install the pinned extractor set.
- Corrected current documentation to distinguish the historical evaluated
  `c4000` semantic space from the configured `c12000` identity, which still
  requires a fresh rebuild and fingerprint-matched evaluation before any
  activation claim.
- Added bounded one-document parser workers (ADR-050) for every filesystem
  parser while preserving the existing `ParserPort` and extraction contracts.
  The M4 Pro 24 GB reference profile runs serially with four native-library
  threads, 6 GiB aggregate process-tree RSS, 120 CPU seconds, 180 wall seconds,
  256 MiB capped file response, bounded diagnostics, process-group teardown,
  open-file/output/core limits, and lower child priority. macOS uses parent RSS
  supervision; Linux adds address/data-space rlimits. Source read-only and
  network denial remain deployment responsibilities. No embedding scope or
  activation policy changed. An independent scoped Luna rerun across 10
  locally allocated samples in all seven configured formats matched raw and
  isolated contracts with unchanged source hashes (8 succeeded, 2 partial,
  0 failed), a 155 MiB peak on that set, and 94 focused tests passing.
- Closed two measured parsing gaps (ADR-049), both decided by A/B study
  against candidates already pinned in-tree, so neither adds a dependency:
  - PDF tables are extracted as additive `pdf_table` evidence units
    (PyMuPDF `find_tables(lines_strict)`, promoted only at rows>=2 and
    cols>=2 to suppress the decorative-box false positives measured on a
    real corpus). `pdf_page` text is byte-identical whether the feature is
    on or off, detection failure degrades to a `TABLE_DETECTION_FAILED`
    warning, and `[parsers.pdf] tables_enabled` (default on) lets operators
    turn it off — measured cost is ~150 ms/page versus ~4 ms/page baseline.
    kordoc was measured and rejected for this path (95.8% false-table rate
    on a real document, cannot open encrypted PDFs, all-or-nothing failure
    on damaged files).
  - HWP/HWPX evidence units now carry a real `section` index, reconstructed
    in-process and verified byte-for-byte against the parser library's own
    output; on any mismatch the locator falls back to `section: null` with a
    warning instead of guessing, and the extracted text never changes.
    Losing the label no longer downgrades the extraction to `partial`.
- Fixed parser defects found by adversarial and realistic-input testing:
  DOCX footnote/endnote text was silently dropped at full reported quality
  and `w:noBreakHyphen` glued words together; a leading comment line made a
  CSV parse into a single column at quality 1.0 with no warning; control-byte
  binary that decodes as valid UTF-8 passed as clean text and is now flagged
  `BINARY_SUSPECTED`; XLSX hidden sheets are flagged; PPTX grouped-shape
  geometry is converted to slide-absolute coordinates instead of reporting
  local coordinates as if they were slide coordinates.
- Fixed two latent bugs in the HWP command-broker path: it read kordoc
  `section`/`sectionNumber` keys that kordoc never emits, and a locator
  `page` value carried no indication of whether it was an exact page or a
  section approximation (`page_mode` now always accompanies it).

- Hardened the system against a nine-lane adversarial audit (ADR-048):
  - Auto-approve precision is now tamper-resistant (a dedicated
    `assertion_candidates.auto_approved` column via migration 0023, not a
    spoofable free-text note prefix) and revocation-aware (a later
    revocation counts the original approval against precision); the policy
    defaults **off** (opt-in) and guided setup no longer silently enables
    candidate promotion.
  - Ontology mutation (review approve/reject, revoke, mining, entity
    create, entity-candidate review) now requires the admin role at the
    shared application layer, so CLI, REST, and MCP all fail closed for
    non-admins; `graph_neighbors`/`graph_path` require admin to request
    `approved_only=false`.
  - CLI/REST/MCP present the same `kip.envelope.v1` contract: REST wraps
    the 413 size guard and any unhandled exception (no bare traceback),
    MCP wraps every tool result and error (typed `code`, not an opaque
    ToolError — an MCP output-shape change), the CLI maps raw pydantic
    validation errors to `validation_error`/exit 3, and the OpenAPI
    version tracks the package version.
  - Ontology loading/release fail closed: a null-valued predicate no
    longer crashes container startup, a corrupt/invalid pending-release
    journal is re-validated on a shadow tree and quarantined instead of
    written or crashing every startup, and discovery candidates carry the
    proposal's field validators.
  - Parsers always fail as typed `ParserError` and no longer silently lose
    content at high confidence: broadened pymupdf/`ElementTree` exception
    capture, CSV `\r`-only handling, UTF-16/NUL detection with NUL-safe
    bodies, DOCX nested-table/textbox and `mc:AlternateContent` recovery,
    content-derived quality, a file-size backstop, a shared zip-bomb and
    recursion-depth guard, and a `csv_full_table_required` refusal for
    aggregate questions answered from a partial CSV chunk.
  - Ingestion isolates a bad file instead of aborting the whole sync,
    stops false-tombstoning present-but-skipped (oversize/filtered) files,
    and retries jobs with bounded backoff.
  - Config cleanup: removed dead keys and orphaned Neo4j env/compose
    artifacts; the setup writer emits the promoted bm25 reranker.

- Fixed silent Korean-encoding corruption in text parsing: plain text,
  Markdown, and CSV now decode through a bounded ladder (BOM strip, UTF-8
  strict, CP949 strict, then a visible degraded fallback with
  `ENCODING_UNCERTAIN` warnings, replacement-ratio-derived quality, and
  `partial` status) — a CP949 CSV previously indexed as mojibake with
  quality 1.0 and no warning.
- Added a structural CSV parser: sniffed delimiter (comma/semicolon/tab),
  header-column metadata, row-boundary chunking with `csv_rows`
  start/end-row locators, ragged-row warnings without file failure; CSV
  numeric values are indexed verbatim.
- Rebuilt the DOCX parser structurally: paragraph-range chunk locators
  (`docx_paragraphs`) replace the single whole-document unit; tables become
  dedicated units with gridSpan/vMerge-safe rendering; header/footer parts
  are extracted; heading levels, hyperlink targets, and image counts land
  in metadata; text boxes are extracted exactly once as `docx_textbox`
  units (fixing measured mc:Choice/Fallback duplication); malformed
  optional parts degrade to `partial` per part. Re-extract existing DOCX
  content to benefit.
- Made parser quality scores content-derived for XLSX (sheet success ×
  replacement penalty; a corrupt sheet now degrades instead of aborting
  the workbook), PPTX (part-failure ratio × replacement penalty), HWP
  (shared hangul/printable formula), and text/CSV (replacement ratio), so
  the `parsers.minimum_quality_score` gate can actually detect degraded
  extractions; clean-file scores are unchanged.
- `kip doctor` now verifies Kordoc OCR resolvability when
  `parsers.ocr.kordoc.enabled` is true (actionable warning instead of
  every image-bearing PDF/PPTX silently degrading to `partial`).
- Added a measured, audited auto-approve policy for low-risk mined
  relations (ADR-047; architecture rule 10 reworded with owner sign-off):
  a candidate whose predicate is `review: not_required`/`risk: low`
  auto-approves only when the miner's per-predicate precision over at
  least 20 human decisions clears 0.95 and the candidate clears 0.8
  confidence (`[ontology.auto_approve]`, enabled by default, fail-closed
  on every axis, human decisions only in the statistic); auto-approvals
  run through the standard approval path, carry the
  `auto-approve-policy/v1` marker with the measured precision, appear in
  the mining result payload's additive `auto_approved` list, and stay
  revocable.
- Raised the embedding input cap `models.embedding.max_document_chars`
  4000 -> 12000 (ADR-035 amendment): the cap is part of the embedding
  space name and identity, so the change yields a fresh shadow space
  through the normal rebuild/evaluate/activate governance and can never
  mix truncation regimes.
- Raised the clarification choice cap from four to eight
  (`CLARIFICATION_CHOICE_CAP`, ADR-032 amendment), applied coherently to
  multi-select answers and remembered preference values; clarification
  and preference contracts regenerated.
- Annotated superseded ADRs (003 -> 046, 018/031 -> 034 with re-test
  triggers, 025 -> 047, 032/035 amendments) so decisions and current
  mechanisms stay traceable.
- Consolidated the adapter seams for tool swappability (ADR-046): removed
  the decorative graph port/adapter/config triad (`capabilities.
  graph_backend` now reports the active repository backend; the dead
  `[graph]` config section and unused `neo4j` packaging extra are gone);
  parameterized the Postgres embedding projection over provisioned
  dimensions (1024 and new migration-0022 1536 table with HNSW; unsupported
  dimensions fail with the provisioned list; status queries now count every
  provisioned table); genericized event-connector fan-out (one
  `sync_remote`, adapter-declared event families, enum-derived
  capabilities — a new connector needs zero application edits); moved
  representation-role and XLSX range-read capability behind their ports;
  documented and defensively enforced the reranker best-first ordering;
  switched generation-provider dispatch to an exhaustive enum; hoisted the
  graph-path result cap into a shared domain constant (fixing a memory
  adapter bug that let a single BFS fan-out exceed the cap); added a
  memory/Postgres behavior-parity contract suite and extended the layering
  rule check to the domain layer and direct vendor-SDK imports;
  `scripts/semantic-smoke.sh` honors `KIP_EMBEDDING_DIMENSIONS`.
- Excluded the private reviewed golden corpus
  (`evaluation/golden/private-onedrive-nl.yaml` + floor file) from the
  starter release bundle; the release verifier now fails a bundle that
  contains it, and the redacted `private-starter.yaml` sample remains
  shipped intentionally.
- Hardened ontology discovery auto-release: materialization is serialized
  by a per-root file lock (concurrent approvals no longer lose a release),
  a two-file predicate release is journaled and self-heals after a crash
  instead of leaving an unloadable tree, predicate shadow validation now
  uses the configured domain profile (domain-profile entity types are
  approvable in `domain`/`range`), reusing an already-released symbol with
  different content is refused as a conflict, and re-proposing a still
  `proposed` candidate refreshes its label/definition/spec instead of
  silently keeping the first version.
- `kip doctor` now checks ontology-root writability when adaptive
  discovery is enabled and reports a leftover pending-release journal;
  `kip evaluate draft promote` reports `comments_discarded` when rewriting
  an annotated dataset.
- `scripts/install-launchd.sh` resolves and bakes `KIP_CONFIG` into every
  plist (explicit env, else the guided-setup generated config, else
  `config/kip.toml`), so launchd jobs no longer silently ignore guided
  setup; `deploy/production.env.example` documents the required
  `KIP_ONTOLOGY_PATH`; `.env.example` documents `KIP_ROLES`,
  `KIP_PROJECT_ROOT`, and `KIP_DATABASE_POOL_MAX_SIZE`.
- `scripts/verify.sh` runs pytest through `uv run` when available
  (matching CI import semantics) and warns loudly when ruff/mypy are
  missing instead of silently skipping them.
- Aligned the container profile with ADR-034 and the example
  configuration: `models.reranker.backend = "bm25"` and
  `search.context_item_max_chars = 16000`; fixed the last stale mining-cap
  literals (`OntologyRagUseCases` and `RelationMiningRequest` defaults now
  match the raised configuration values) and added drift-protector tests
  (skill-mirror walk, capacity-default parity across model/CLI/MCP/setup,
  VERSION/pyproject, CI/production Postgres image digest, MCP tool list
  documentation).
- Added judge-proposed golden-dataset growth (ADR-045): an LLM judge
  authors `kip.golden-draft.v1` case proposals with per-case confidence
  and rationale; `kip evaluate draft validate/review/promote` runs the
  human sample-audit and fail-closed promotion (refused below the minimum
  sample rate, on any sampled rejection, or on ID collision) into a
  reviewed golden dataset, with judge provenance recorded per case and
  draft/review schemas published under `contracts/`. Canonical-authority
  fields (`lifecycle`/`version`/`reviewer`/`source_revision`) cannot be
  judge-proposed — a draft setting them is rejected — and are assigned at
  promotion from explicit options and the auditing reviewer, so promoted
  datasets satisfy `gate_eligible`. The judge generates; only
  human-authorized promotion creates canonical truth.
- Raised capacity defaults sized for older model context windows
  (`docs/plans/2026-08-14-llm-capability-scaling.md`): context pack
  40k -> 120k chars (item cap 8k -> 16k), answer evidence budget
  12k -> 32k chars (ceiling 40k -> 200k), generation 8 -> 16 claims and
  1024 -> 4096 output tokens, relation mining 50 -> 200 units /
  120k -> 480k chars / 32 -> 128 entity and 64 -> 256 relation proposals,
  ontology answer context 8 -> 16 entities and 50 -> 150 edges. All remain
  config keys with unchanged code-side hard bounds.

## 3.3.0 - 2026-08-14

- Made ontology discovery approval materialize an additive release
  automatically (ADR-044): accepting an entity-type or predicate candidate
  writes the symbol into the ontology YAML tree with comment-preserving
  targeted edits, a minor version bump, review-policy sync, shadow
  validation, atomic apply, idempotent retries, and fail-closed behavior on
  read-only roots; discovery proposals gained optional spec fields
  (`parent`, `domain`, `range`, `inverse`, `risk`, `review`, `extraction`)
  on CLI/REST/MCP, persisted by migration 0021; auto-released predicates
  default to review-required/high-risk; review responses carry a
  `kip.ontology-release.v1` payload with `catalog_refresh:
  "restart_required"` for long-running processes; candidate-proposal
  surfaces now default `ontology_version` to the active catalog version.
- Made the ontology contract writable in containers for auto-release: the
  image no longer bakes `/app/ontology` immutable, and both Compose profiles
  bind-mount the version-controlled checkout (`KIP_ONTOLOGY_PATH`, default
  `./ontology` in development, required in production) read-write into the
  API and read-only into the worker, so approved releases persist across
  restarts and stay reviewable through git history.
- Required a `CHANGELOG.md` entry for every user-visible behavior, contract,
  configuration, or deployment change in the `AGENTS.md` change workflow.
- Enabled the conversational curation loop by default in the shipped
  configurations: `interaction.enabled = true` and
  `ontology.adaptive_discovery = true` in the example and container
  profiles; guided setup still records the explicit consent decision and
  `disabled` remains supported.
- Hardened the ontology contract validation (ADR-043): a domain profile that
  redefines a core entity type or core predicate name, and a
  `sources/*.yaml` object type whose `parent` is not a known entity type,
  now fail `kip ontology validate`, `OntologyRelease.load`, and container
  startup instead of silently shadowing core semantics.
- Added `POST /v1/review/candidates` (admin), the REST equivalent of CLI
  `review propose`: records a human-origin assertion candidate that enters
  the same review queue and is never auto-approved.
- Added the missing `limit` parameter to the MCP `kip_graph_neighbors` tool,
  matching the CLI and REST graph-neighbors contract.
- Added a real-PostgreSQL cross-scope ACL integration test proving that a
  principal holding one scope cannot see another scope's content through
  search, direct unit read, or graph neighbors/path.
- Removed configuration keys that were documented but never read
  (`database.pool_min`/`pool_max`, `storage.snapshot_*`,
  `security.max_zip_*`, `search.lexical_limit_default`, `graph.max_*`) from
  the example and container configuration, and set the real
  `database.pool_max_size` in the container profile, which previously fell
  back silently to the built-in default.
- Added structured PPTX extraction (ADR-041): pinned `python-pptx` plus a
  bounded read-only OOXML scan emit typed shape-level units for text, merged
  tables, chart caches, images (with SHA-256), speaker notes, legacy comments,
  and SmartArt, preserving slide/shape IDs, group paths, EMU geometry, source
  z-order, and a geometry-derived reading order; malformed optional parts keep
  primary content with a `partial` extraction and typed warnings; macros,
  external relationships, and embedded OLE objects are never executed or
  expanded, and ZIP entry/size/ratio limits apply before model construction.
- Added Korean OCR candidate enrichment (ADR-042): a typed optional `OcrPort`
  backed by a version-pinned Kordoc 4.7.3 executable (PP-OCRv5 Korean) appends
  `pdf_ocr` units with page and pixel bounding boxes for low-text, private-use,
  control-character, or replacement-character PDF pages and OCRs bounded PPTX
  images, while native `pdf_page` and shape units stay canonical.
- Made the ontology curation loop reviewable end to end (ADR-038): the mining
  job digest includes the approved-entity-set hash so the two-pass
  mine -> approve entities -> re-mine loop actually runs; invalid, duplicate,
  and stale-evidence proposals are skipped with per-proposal reasons recorded
  on a durable `kip.ontology-mining-result.v1` job payload (visible via
  `kip jobs list`, `GET /v1/jobs`, and the new MCP `kip_jobs` tool); evidence
  and review enforcement is derived from the ontology catalog and pinned to
  `predicates.yaml` by a contract test; candidate listings return the
  triage-ordered `kip.assertion-candidate-listing.v1` contract with display
  names, Korean predicate labels, and ACL-gated evidence snippets;
  `include_candidate_assertions` now surfaces clearly-marked proposed
  candidates on ontology-context-bearing surfaces; and Korean
  `label_ko`/`description`/`description_ko` metadata covers all predicates and
  entity types without an ontology version bump.
- Added assertion review governance: migration 0019 revocation audit columns,
  `kip review revoke`, `POST /v1/review/assertions/{id}/revoke`, MCP
  `kip_ontology_assertion_revoke`, exclusion of revoked assertions from every
  approved-only surface, and `--supersede-contradicted` on approval.
- Added filesystem deletion reconciliation (ADR-039): migration 0020 absence
  tracking, `[sync] deletion_grace_scans` (default 2), complete-scan-only
  fail-safe semantics (failed scans mark nothing; empty scans skip with a
  warning), soft tombstone revisions through the shared ingest path,
  reappearance re-indexing, and `absent`/`tombstoned` sync-summary fields.
- Fixed PostgreSQL re-sync of unchanged files failing with an ACL-snapshot
  ConflictError on every second run: configuration-owned snapshot timestamps
  refresh while snapshot identity fields stay strictly verified.
- Made guided setup end in a runnable deployment (ADR-040): fail-fast
  Python 3.12+ bootstrap, `env:`/`file:`-only secret references rejected at
  answer time, runtime-readiness verify checks, receipt `next_steps`, the
  host-path `config/kip.host.generated.toml` selected by `.mcp.json`, and
  `scripts/app-up.sh` layering the generated Compose override; the generated
  default search mode is `reranked` per ADR-034.
- Hardened the production topology: read-only `${KIP_NAS_PATH}` bind on the
  API service, a `/readyz` database round-trip endpoint and healthcheck
  (`/healthz` stays liveness-only), a worker database-connectivity
  healthcheck, and resource limits on all services.
- Added operations tooling: backup `--retain` retention pruning and
  `--dry-run`, redacted configuration snapshots with a seal-and-verify secret
  rescan, launchd daily backup and optional `ops-report.sh` scheduling with
  generated newsyslog rotation and a double-worker guard, and
  `scripts/ops-report.sh` (failed jobs, queue age, sync age, disk free,
  backup age, API health, `--json`, `KIP_OPS_WEBHOOK`).

## 3.2.0 - 2026-08-10

- Split application capabilities and repository ports so CLI, REST, MCP, and
  adapters compose through strict typed boundaries without vendor leakage.
- Added resumable agent-guided setup with one-question inspection, bounded
  source inventories, read-only mounts, secret references, deterministic plans,
  atomic apply/verify receipts, and generated MCP runtime configuration.
- Added trusted API-key/JWT identity, fresh ACL snapshots, classification-aware
  model egress, structured OpenAI/Anthropic generation, exact-evidence claim
  verification, and redacted RAG telemetry.
- Added typed ontology entity and relation candidates, durable relation-mining
  jobs, human review, approved graph context in answers, ontology migration
  materialization, and reviewed answer/ontology evaluation gates.
- Added a digest-pinned non-root container, hardened production Compose profile,
  hash-locked dependencies, deterministic wheel/starter bundle, SPDX SBOM,
  SLSA provenance, secret/private-data scans, and tag-only attested publishing.
- Added sealed PostgreSQL/CAS/config backup, explicit empty-target restore,
  manifest and RLS verification, projection rebuild, evaluation comparison, and
  checksummed recovery-drill receipts.

## 3.1.0 - 2026-07-28

- Added PostgreSQL 18 canonical store and pgvector-capable reference image.
- Added shared application layer for CLI, REST, and optional MCP.
- Added root `AGENTS.md`, root `CLAUDE.md`, project Skill, and root `.mcp.json`.
- Added filesystem, Slack, IMAP, Apple Mail, HWP broker, PDF, and XLSX adapter boundaries.
- Added shallow XLSX index and exact workbook range reader.
- Added ontology, assertion candidate, approved assertion, ACL, and graph query contracts.
- Added optional Neo4j projection adapter boundary without making it canonical.
- Added source-neutral `sync run`, stable top-level `xlsx-read`, projection
  management, canonical export, and assertion explanation commands.
- Added REST connector events, generic durable sync enqueueing, job polling,
  assertion explanation, OpenAPI contracts, MCP tools, and a Python
  application client.
