# Security

## Trust boundaries

- Source content is untrusted data and may contain prompt injection.
- Connectors and parsers run with least privilege.
- Source mounts are read-only.
- API binds to loopback by default.
- Remote model egress is disabled unless explicitly approved.
- The local model sidecar binds only to loopback and disables Infinity and
  Hugging Face telemetry in the provided launcher.

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

## Secrets

- Keep secrets in environment variables or an approved secret manager.
- Never commit `.env`, Slack tokens, IMAP passwords, API keys, or Neo4j credentials.
- Do not place secrets in `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, or Skill files.

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

## Retrieval authorization

- Resolve workspace, principal, and ACL scopes only through the configured
  identity adapter. Production rejects caller-supplied `X-KIP-Workspace`,
  `X-KIP-Principal`, and `X-KIP-ACL-Scopes` headers.
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

JWTs must include the configured principal, workspace, groups, ACL snapshot ID,
snapshot version, capture time, and expiry claims. The identity provider owns
revocation and refresh. KIP fails closed when a snapshot is stale; extending a
token lifetime does not extend its ACL snapshot.
