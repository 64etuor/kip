# Connectors

## Connector contract

A connector discovers immutable source revisions and emits canonical source events. It must provide stable external IDs, cursors, ACL scope, and deletion semantics. It must not parse business meaning.

## Filesystem/NAS

Only regular files are eligible for byte reads. FIFOs and other special items
are counted as `non_regular` skips and remain present for deletion accounting.

- Mount read-only.
- Use path relative to configured source root as the external ID.
- Enabled `sources.filesystem` entries are the deployment's allowed roots,
  not just scan hints. Retrieval requires the current source name, ACL snapshot,
  and a stored descendant path; exact reads also check live path containment.
  After removing, disabling, moving, or narrowing a source, reload services and
  explicitly sync the retained scope before expecting its old index to return.
  Request ACL scopes cannot broaden this boundary (ADR-056).
- Wait for file size and mtime to settle before hashing.
- Inspect cloud residency from filesystem metadata before hashing or parsing.
  OneDrive/cloud-only placeholders are deferred without downloading them;
  choose files in the provider client and make them available locally first.
  Symlinks are skipped by default; an enabled in-root symlink policy still
  rejects outside-root targets and directory cycles. Scan warnings aggregate
  skip reasons instead of returning one warning per excluded file.
- Do not treat an unavailable mount as mass deletion. A failed or aborted scan
  never contributes deletion evidence, and a scan that sees zero files skips
  deletion reconciliation entirely.
- Treat a directory walk error as an incomplete scan. Files deferred by settle,
  symlink, cloud-residency, filter, or size policy remain present for deletion accounting even
  though they are not parsed in that scan.
- Hash content for revision identity.
- Deletion is reconciled per complete scan with a grace policy
  (`[sync] deletion_grace_scans`, default 2): an indexed file absent from that
  many consecutive complete scans is soft-deleted through the same immutable
  tombstone-revision path used by event connectors. Prior revisions and
  approved assertions are preserved; a reappearing file clears its absence
  mark and is re-indexed. See `docs/OPERATIONS.md` "Filesystem deletion grace
  policy" for the operational details.

### Parser process isolation

The reference profiles wrap every registered filesystem parser behind the
same `ParserPort` adapter and execute one document in a fresh bounded child.
The M4 Pro 24 GB profile is intentionally serial: four native-library threads,
6144 MiB aggregate process-tree RSS, 120 CPU seconds, 180 wall seconds, a
256 MiB response cap, and nice 5. The parent kills the full process group on a
timeout or memory breach, validates the versioned response file, and exposes a
typed per-file parser failure without replacing an active extraction.

This changes execution containment, not format coverage or embedding policy.
The child opens the configured source path, so the source root must still be
mounted read-only. Network denial also remains an outer runtime/container
responsibility.

## HWP/HWPX parser broker

The reference profile uses the native `hwp-hwpx-parser` adapter first, inside
the same bounded parser child used for the other filesystem formats.
Command parsers are optional, explicitly installed fallbacks; their templates
remain configuration, not Core code. Runtime indexing must never download or
execute an unpinned package installer.

```toml
[parsers.hwp]
order = ["hwp-hwpx-parser", "kordoc", "unhwp", "paired_pdf"]

[parsers.hwp.hwp-hwpx-parser]
enabled = true
max_chars_per_unit = 4000

[parsers.hwp.kordoc]
enabled = false
argv = ["kordoc", "{input}", "--format", "json"]

[parsers.hwp.unhwp]
enabled = false
argv = ["unhwp", "convert", "{input}", "-o", "{output_dir}", "--all"]
```

Validate command fallbacks against the exact installed version before enabling
them. The broker records parser/version/output hash. A failed or below-threshold
candidate never replaces the successful active extraction.

Kordoc-compatible JSON blocks retain structured `table`, `imageData`, `spans`,
`footnoteText`, list, link, style, and child metadata instead of reducing every
block to its `text` field. Tables additionally render deterministic TSV search
text, images emit a filename placeholder, and structured warnings retain code
and page context.

When an existing index must adopt a new HWP parser, prepare the entire source in
non-mutating shadow mode first. Activation is a separate operator action:

```bash
./scripts/kip parser reextract --source company-nas
./scripts/kip parser reextract --source company-nas --activate
```

By default the operation hashes only configured HWP/HWPX candidates. Add a
repeatable `--extension` (for example `--extension .pdf`; the leading dot is
optional and matching is case-insensitive) to re-extract another format after
its parser changes; an extension without a registered parser is rejected, and
the JSON summary lists the scanned `extensions`. Activation inherits
and rechecks the current canonical source ACL snapshot and classification,
retains extraction history, replaces only the rebuildable active lexical
projection, and commits each document atomically. When semantic search is
enabled, the activated run then embeds the new or changed units into the
semantic projection and reports it in the summary's optional
`semantic_projection` object, as every sync does (ADR-065); a model runtime
that is down leaves `status = "unavailable"` without failing the run. It never
writes to the source file or changes source access policy.

Keep production parser versions pinned. Upgrade a pin only after the parser conformance corpus and shadow-extraction comparison pass; do not use `@latest` in scheduled indexing.

## PPTX parser

Install `python-pptx` through the pinned `extractors` extra and add `.pptx` to
the filesystem source. The native adapter, invoked inside the bounded parser
child, emits shape-level evidence for text, merged tables, chart caches, image
metadata, nested groups, notes, comments, and SmartArt. It preserves exact
slide/shape coordinates and never fetches external relationships or executes
macros. Optional OOXML failures are visible as partial warnings; embedded
objects are counted but not expanded. Legacy `.ppt`, media transcription, and
modern threaded comments require a separate evaluated adapter.
The base wheel can start its CLI without the extractor extra; Pillow is loaded
only when PPTX OCR must transcode a non-PNG/JPEG/WebP image. Actual PPTX parsing
and production indexing still require the pinned `extractors` extra.

## Local PDF backends

New profiles select `pdf_inspector`, a pinned local Rust extension that emits
per-page Markdown, layout/table signals, and OCR reasons without network calls.
Valid Markdown tables become additive table units. A detected table page whose
Markdown has no valid table block runs the existing PyMuPDF `lines_strict`
fallback for that page only. Set `backend = "pymupdf"` to roll back; do not
silently mix backends after a parse failure (a PDF that pdf-inspector cannot
parse is recorded as failed, with no per-document PyMuPDF fallback). Search
text and reranker input drop paired Markdown emphasis and inline presentation
tags (pdf-inspector units only; unpaired asterisks stay), while the unit
body returned by `read` keeps the extractor Markdown exactly. The pinned
pdf-inspector 1.19.0 ships optional OCR of its own; KIP calls only
`extract_pages_markdown` and sends OCR candidates to Kordoc. Existing indexed
PDFs require shadow re-extraction with `parser reextract --extension .pdf`
before activation because page bodies change from plain text to Markdown, and
a pdf-inspector pin upgrade leaves existing extractions unchanged until they
are re-extracted the same way.

## Local Korean OCR adapter

PDF and PPTX parsers share the default Kordoc 4.13.1 adapter in new reference
installations. Bootstrap installs the exact runtime and model cache:

```bash
./scripts/install-kordoc.sh
./scripts/kordoc models --status
```

The launcher sets `KORDOC_OFFLINE=1` for indexing. The registry requires
`argv`, `version_argv`, and `expected_version = "4.13.1"`, and rejects `npm` or
`npx` as the runtime command. PDF OCR runs only when native text quality crosses
a candidate signal. PPTX OCR batches eligible pictures, deduplicates identical
bytes, and applies count and byte budgets from `[parsers.ocr.pptx]`. Both paths
append located candidate units and preserve native units on failure.
Existing local configurations retain their explicit enabled/disabled value and
must be upgraded deliberately. An `expected_version` of `4.8.0` or `4.7.3`
written by an earlier KIP release resolves to the current pin; any other
non-current value is rejected.

## Slack

- Prefer conversations history/replies and event revisions over Slack search.
- Record workspace, conversation, timestamp, thread root, edit timestamp, and deletion tombstone.
- Respect channel membership and token scopes.
- Download files only when explicitly enabled; otherwise retain metadata and a protected source URI.

## Apple Mail

- Use the macOS JXA adapter or a server protocol connector.
- Configure account and mailbox allowlists.
- Grant Mail Automation permission to the host process.
- Do not read `~/Library/Mail` databases directly in the normal path.

## IMAP

- Use UID-based cursors, not sequence numbers.
- Store Message-ID, In-Reply-To, References, mailbox placement, and RFC822 hash.
- Separate a message from its mailbox placements.

## Push connector API

External applications may send canonical `ConnectorEvent` objects to `POST /v1/connectors/events`.

- Use a stable `event_id` and stable `external_id` for every source object.
- Replaying the same operation and payload is idempotent because the canonical revision hash is unchanged.
- Authenticate with both the API key and admin key in the starter profile.
- Propagate the source ACL in `acl_scopes`; never grant broader access than the originating system.
- Treat `delete` as an immutable tombstone revision rather than physically deleting prior evidence.

Example:

```bash
curl -sS http://127.0.0.1:8080/v1/connectors/events \
  -H 'Content-Type: application/json' \
  -H "X-KIP-API-Key: $KIP_API_KEY" \
  -H "X-KIP-Admin-Key: $KIP_ADMIN_KEY" \
  --data-binary @examples/connector/event.json
```

The authenticated server identity authorizes the write. Source ACLs belong in
the versioned connector event payload and are validated by the connector
contract; they are not caller identity headers.
