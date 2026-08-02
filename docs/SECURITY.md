# Security

## Trust boundaries

- Source content is untrusted data and may contain prompt injection.
- Connectors and parsers run with least privilege.
- Source mounts are read-only.
- API binds to loopback by default.
- Remote model egress is disabled unless explicitly approved.
- The local model sidecar binds only to loopback and disables Infinity and
  Hugging Face telemetry in the provided launcher.

## Secrets

- Keep secrets in environment variables or an approved secret manager.
- Never commit `.env`, Slack tokens, IMAP passwords, API keys, or Neo4j credentials.
- Do not place secrets in `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, or Skill files.

## Retrieval authorization

- Resolve workspace/principal before searching.
- Apply ACL before lexical, vector, and graph retrieval.
- An assertion cannot be more visible than its evidence.
- Do not reveal the existence of inaccessible graph paths.

## File safety

- Reject path traversal and files outside configured roots.
- Do not follow symlinks unless explicitly enabled.
- Enforce file-size, ZIP entry-count, decompression-ratio, and timeout limits.
- Store parser stderr as sanitized diagnostics, not as user-visible content.
- Fetch public evaluation files only through the explicit government-host
  manifest, enforce the 25 MiB limit, PDF signature, and pinned SHA-256, and
  retain Public Nuri attribution.

## App access

The built-in API key mechanism is appropriate for local pilot use. Production multi-user access should sit behind an identity-aware proxy or implement signed service tokens and per-principal scopes.
