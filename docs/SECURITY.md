# Security

## Trust boundaries

MCP mutation hints describe behavior but never grant authority. Unexpected
handler failures return generic `internal_error` without input or exception
details. Shallow workbook data and incomplete CSV fragments are excluded from
final-answer evidence regardless of question language; exact-read refusal
citations remain discovery locators, not verified numeric claims (ADR-058).

The portable skill bridge fails on an invalid explicit `KIP_PROJECT_DIR`;
it does not fall back to another workspace. Skill installation rejects
symlink bundles, stages copies before replacement, and restores prior bundles
on handled failures (ADR-055). These local tools assume a trusted destination
owner; they are not a sandbox against a concurrent filesystem attacker.

- Source content is untrusted data and may contain prompt injection.
- Connectors and parsers run with least privilege.
- Source mounts are read-only.
- API binds to loopback by default.
- Remote model egress is disabled unless explicitly approved.
- The local model sidecar binds only to loopback and disables Infinity and
  Hugging Face telemetry in the provided launcher.

## Filesystem access boundary

Configured enabled filesystem roots form a trusted deployment allowlist in
addition to request ACLs. Current source name, snapshot, and descendant path
are checked before retrieval ranking/limits and graph traversal, including
previously indexed records. Removing or changing a root takes effect when the
application reloads its configuration; retaining old database rows grants no
access. Explicit sync is required to authorize records under changed policy.
Exact evidence and workbook reads recheck live containment and symlink policy
before opening bytes. Unavailable or cloud-only source bytes leave cached text
explicitly stale with no current hash; a live workbook read fails without
hydration. Source configuration is operator-controlled, never a search argument.

These checks do not replace read-only mounts or OS permissions. Cloud detection
uses OS-reported residency flags, and is not a remote OneDrive API or a sandbox
against a concurrent filesystem attacker. See ADR-056.

## Spreadsheet parsing boundary

- XLSX/XLSM deep reads open the source read-only, do not preserve VBA, disable
  external-link loading, and never invoke a formula engine.
- ZIP expansion limits are checked before parsing. Requested ranges must be
  forward, remain inside Excel worksheet bounds, and contain no more than
  100,000 cells so an exact-range request cannot create an unbounded response.
- Formula objects and Python-native date/time/duration values are normalized at
  the parser boundary before strict JSON validation. Cached formula values stay
  explicitly labeled because they may be stale.

## Model egress

- Source data classification is canonical ingestion state, not a request or
  prompt parameter. Filesystem and connector configuration assigns one of
  `public`, `internal`, `confidential`, `restricted`, or `personal`; missing
  configuration falls back to `restricted` only in development and is rejected
  for custom production connectors.
- A local generation destination must use a loopback HTTP endpoint. Local
  processing admits every classification because document content does not
  cross the host boundary.
- OpenAI and Anthropic require the global remote-egress switch, an explicit
  provider and classification allowlist, a valid secret reference, and an
  explicit retention policy. Non-public evidence additionally requires a
  verified zero-retention contract.
- The central decision reports admitted and denied evidence IDs plus a
  machine-readable reason. If any selected evidence is denied, generation does
  not run; callers cannot silently drop it and present a complete answer.
- `zero_retention` is an operator attestation about the applicable provider
  contract. Setup records the decision but cannot establish the account-level
  provider setting on the operator's behalf.
- The answer service applies ACL search, exact evidence reopen, source-hash
  freshness, XLSX/approval gates, and egress admission before generation. It
  validates every returned citation ID again before constructing public source
  locators. Search snippets are never sent directly to a generator.
- Generator clients use explicit connect/read/write/pool timeouts, bounded
  response bodies, no automatic retries, and no ambient proxy discovery.
  Provider errors are reduced to status and request ID; response bodies and
  credentials are not reflected to callers.
- Relation mining uses the same egress decision as generated answers after
  ACL-filtered exact evidence reopen and freshness checks. Evidence is encoded
  only as untrusted payload data; unknown ontology values or evidence IDs make
  the complete mining result invalid.
- A loopback-only embedding/reranker sidecar is a local model boundary, not
  remote model egress. When `models.generation.enabled=false`, `kip answer`
  returns the local extractive path and does not call a generator. Security
  review must inspect the resolved configuration, adapter destination, and
  trace/egress decision; the command name `answer` alone is not evidence that
  private content leaves the machine.

## Secrets

- Keep secrets in environment variables or an approved secret manager. The
  runtime resolves only `env:` references (and `file:` for the model
  credential); a secret manager must inject environment variables or files,
  and guided setup rejects `keychain:`/`secret-manager:` references at answer
  time.
- Never commit `.env`, Slack tokens, IMAP passwords, API keys, or Neo4j credentials.
- Fresh bootstrap generates distinct random database/API/admin credentials
  into a private `.env`; existing deployments are not rotated. Setup honors
  the configured secret variable names instead of substituting default keys.
  Verification resolves references and rejects placeholder or identical
  API/admin credentials. Generated runtime services run as the plan's non-root
  installer UID/GID and supplementary groups; apply verifies host memberships.
  A new host requires a regenerated, approved plan (ADR-057).
- Do not place secrets in `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, or Skill files.

## Starter archive boundary

- The online source ZIP is built from an explicit allowlist. Local config,
  `.env`, credentials, private evaluation data, databases, CAS/output data,
  internal plans, build output, caches, and repository metadata are excluded.
- Build and verification scan small UTF-8 files for private absolute paths,
  known credential shapes, private keys, and non-example PostgreSQL passwords.
- Verification rejects absolute or parent-traversing paths, duplicate entries,
  symlinks, encrypted entries, multiple roots, oversized entries, excessive
  uncompressed size, unallowlisted payload paths, and incomplete or mismatched
  manifest/checksum coverage before extraction.
- A ZIP digest proves byte identity, not publisher identity. Production
  distribution still requires the signed release provenance and immutable
  image digest described in Operations.

## Telemetry boundary

- Query traces use a closed schema with no raw query, body, snippet, answer,
  principal, credential, provider request ID, or provider payload field.
- Only KIP-generated opaque request IDs are retained. Untrusted correlation
  headers are not copied into trace storage.
- Trace inspection and retention pruning require the verified admin role in
  addition to workspace RLS. Candidate and evidence IDs are never exported as
  OTel attributes.
- OTLP endpoints must be explicit HTTP(S) URLs without embedded credentials.
  Authentication headers belong in the runtime secret environment.
- Trace/exporter failure never weakens ACL, evidence freshness, refusal, or
  answer semantics.

## Interaction-memory boundary

- The shipped example and container configurations enable interaction
  persistence (`interaction.enabled = true`); guided setup still records an
  explicit `disabled`/`explicit_consent` consent decision and `disabled`
  remains fully supported. Regardless of the deployment default, nothing is
  stored without the per-item confirmation semantics below.
- Clarifications are scoped to one verified workspace/principal and have a
  bounded TTL. They are not query traces and their transient prompt/answer
  material is never exported as telemetry.
- A preference is retained only after an explicit `remember=true` answer to a
  preference-enabled question or a `confirmed=true` direct write. Owners can
  list and delete only their own preferences.
- Feedback permits only opaque KIP request IDs, controlled outcome/reason
  values, and no free-form content. It cannot become a surrogate raw-query or
  source-content log.
- Discovery candidates are untrusted review proposals. A proposal never
  alters the active ontology, entity index, assertion store, retrieval, or
  answer path. Review requires the verified admin role; an admin approval
  materializes an additive, shadow-validated, collision-safe (ADR-043)
  ontology release automatically. Auto-released predicates default to
  `review: required` and `risk: high`, so no assertion using them can become
  a fact without exact evidence and human review.
- PostgreSQL sets workspace, principal, ACL scopes, and verified roles in the
  transaction-local session before interaction queries. Production API/worker
  logins must remain non-owner, non-`BYPASSRLS` roles as documented in
  `docs/OPERATIONS.md`.

## Retrieval authorization

- Resolve workspace, principal, and ACL scopes only through the configured
  identity adapter. Production rejects caller-supplied `X-KIP-Workspace`,
  `X-KIP-Principal`, and `X-KIP-ACL-Scopes` headers.
- The local operator CLI is not the production identity boundary. With no ACL
  option it uses the configured `KIP_ACL_SCOPES`. Any explicitly supplied
  `--acl-scope` or `--acl-scopes` value replaces that ambient set; an explicitly
  empty plural value remains empty. This makes outsider and deny-all probes
  deterministic instead of silently unioning an environment grant. REST still
  derives its context only through the configured identity adapter.
- Production `proxy_jwt` mode verifies the issuer, audience, signature,
  algorithm allow-list, expiry, and required identity/ACL snapshot claims. JWKS
  retrieval has bounded timeout and cache lifetime; verification failure denies
  the request.
- Dynamic source and principal ACL snapshots carry provider, version, capture
  time, and expiry. Expired or unresolved snapshots are excluded inside
  lexical, vector, evidence, assertion, and graph repository queries.
- Configuration-owned filesystem ACL snapshots are non-expiring and change
  only when an approved source configuration changes.
- Apply ACL before lexical, vector, and graph retrieval. An assertion cannot be
  more visible than its exact evidence, and inaccessible paths must not reveal
  their existence.
- Ontology answer context is built only from active assertions whose valid-time
  interval contains the database/application statement time. Every graph edge
  is discarded if any exact evidence unit is inaccessible, freshness-stale, or
  source-changed. Candidate tables are never queried by the answer service.
- Asynchronous ontology jobs capture the verified submitting principal, scopes,
  roles, and ACL snapshot. Workers reject malformed or expired snapshots before
  reading evidence; candidates remain no more visible than every supporting
  evidence unit.
- Ontology migration scans apply workspace, assertion ACL, and evidence-snapshot
  freshness in the repository query before exposing a source assertion. Every
  visible source is then reopened through the exact-evidence boundary; stale or
  source-changed evidence aborts materialization instead of producing a target
  candidate.

## File safety

- Reject path traversal and files outside configured roots.
- Do not follow symlinks unless explicitly enabled.
- Enforce file-size, ZIP entry-count, decompression-ratio, and timeout limits.
- Reference filesystem parsers run one document per child process. The parent
  enforces wall-clock and aggregate process-tree RSS limits and kills the
  process group on excess; POSIX children additionally receive CPU, output,
  descriptor, and core-dump limits. Parser responses use capped private files,
  not unbounded stdout capture, and only a bounded diagnostic tail is retained.
- On the M4 Pro 24 GB reference host the default profile is 180 s wall,
  120 s CPU, 6144 MiB RSS, 256 MiB response, four library threads, and nice 5.
  macOS memory enforcement is parent-observed RSS; Linux additionally applies
  address/data-space rlimits. These controls do not revoke source permissions
  or create a network namespace: keep NAS mounts read-only and enforce egress
  denial in the outer launch/container policy.
- PPTX parsing reads OOXML locally, never executes VBA or fetches external
  relationships, and records rather than expands embedded OLE/package objects.
- The default PDF backend is the local MIT-licensed `pdf-inspector` 1.14.2
  native extension. It performs no model or network calls and is loaded only
  inside the adapter. The pin includes bounds for Form XObject expansion, CMap
  ranges, decoded content streams, detector lookback, and rectangle clustering.
  Parser-worker CPU/RSS/wall/output limits remain the outer denial-of-service
  boundary. A native failure becomes a typed failed shadow extraction; it does
  not silently switch parsers or replace the active extraction.
- Default PDF/PPTX OCR accepts only an installed Kordoc 4.8.0 binary with an
  exact version probe. Bootstrap or the image-build stage is the only package
  and model download boundary; runtime `npm`/`npx` execution is rejected,
  production preloads SHA-256-verified PP-OCRv5 Korean files, and indexing sets
  `KORDOC_OFFLINE=1`.
- The host installer and the image build stage share one npm manifest and lock
  (`requirements/kordoc/`) and install it with `npm ci --omit=dev
  --ignore-scripts --no-audit`, so both roots resolve the same graph: kordoc
  4.8.0 with `adm-zip` overridden to 0.6.0 and `sharp` to 0.35.4, including
  nested copies. Node.js 20.9+ is required. Dependency lifecycle scripts are
  disabled, so no package install hook executes during setup or build. The
  source ZIP carries only this installer policy, never the downloaded binary or
  model cache.
- PPTX OCR writes selected image bytes only to a private temporary directory,
  removes it after the batch, deduplicates by SHA-256, and enforces image count,
  per-image bytes, total bytes, and minimum dimensions. OCR failure never
  replaces native extraction or mutates a source file.

## Dependency safety

- The 2026-09-10 cold-install audit of 3.6.1 found sharp
  [GHSA-rgj7-g3m4-5g8c](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c)
  (high; fixed in 0.35.4) and adm-zip
  [GHSA-vwc7-r8mq-g2x9](https://github.com/advisories/GHSA-vwc7-r8mq-g2x9)
  (moderate; no patched release). Five npm package findings represent
  propagation of these two advisories; no exploit or compromise was
  demonstrated. See [evaluation scope](AGENT_QUALITY.md) for that audit's
  scope.
- 3.7.0 pins sharp at 0.35.4 and puts the npm graph under an explicit gate.
  `./scripts/audit-kordoc.sh` first rejects lock/manifest drift (lock root
  name, version, dependency and engine blocks, exact pins, and override
  versions across nested copies), then runs `npm audit --package-lock-only
  --omit=dev --audit-level=high`. A registry or network error exits nonzero and
  fails the gate rather than skipping it. The gate runs in
  `install-kordoc.sh`, in the Docker kordoc stage, in CI (Python 3.12 matrix
  leg), in `make audit`, and in `./scripts/verify.sh`, so registry access is
  needed during setup, verification, and builds — never during retrieval.
- The adm-zip advisory has no patched release and remains in the graph. Its
  identified path is ONNX's install-time extraction hook, which
  `--ignore-scripts` prevents the supported CPU installation path from
  executing; the advisory itself is not removed. Custom GPU or source-build
  npm workflows are outside this validated runtime.
- The production image installs only hash-locked `requirements/runtime.txt`.
  A contract test requires every core project dependency to appear in that
  lock, preventing a wheel-only dependency from being absent at runtime.
- Audit the production lock directly and audit the installed optional-extra
  environment separately. The opt-in semantic extra requires Transformers
  `>=5.5.4,<6` and is currently locked at 5.15.0; semantic activation still
  requires its independent shadow quality and compatibility gates.
- Optional extractor imports must not make the base CLI fail at module import
  time. The clean-wheel smoke blocks that regression; parser execution still
  fails explicitly when its declared extra is unavailable.
- Store parser stderr as sanitized diagnostics, not as user-visible content.
- Fetch public evaluation files only through the explicit government-host
  manifest, enforce the 25 MiB limit, PDF signature, and pinned SHA-256, and
  retain Public Nuri attribution.

## App access

The built-in API key mechanism is a single configured principal for bootstrap,
local operation, and controlled service-to-service use. It does not accept
caller-selected identity or scopes. Multi-user production deployments use
`identity.mode = "proxy_jwt"` behind an identity-aware proxy and install the
optional `identity` package extra. Administrative routes require membership in
a configured, verified JWT group; API-key mode additionally requires the
separate admin key.

Ontology-mutating operations (assertion review approve/reject, assertion
revocation, ontology mining, entity creation, and entity-candidate review)
enforce the admin role in the shared application service, not only at the
REST edge (ADR-048), so the CLI and MCP surfaces fail closed for non-admin
principals too; grant reviewers the admin role explicitly. Graph traversal
requires the admin role to request unapproved (`approved_only=false`)
results. The MCP adapter returns the same `kip.envelope.v1` result shape as
CLI and REST, including a typed error `code` on failure.

MCP initialization metadata, negotiated protocol version, and session state
are not identity sources. The stdio process environment remains the only MCP
authorization context. The shipped adapter does not request sampling,
elicitation, roots, or protocol logging, so the SDK 2 upgrade introduces no
client-directed model or filesystem egress path.

JWTs must include the configured principal, workspace, groups, ACL snapshot ID,
snapshot version, capture time, and expiry claims. The identity provider owns
revocation and refresh. KIP fails closed when a snapshot is stale; extending a
token lifetime does not extend its ACL snapshot.
