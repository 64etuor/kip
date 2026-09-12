# Evidence, freshness, and refusals

## Response shapes

Every edge returns `kip.envelope.v1`: `ok`, `data`, `error`, `meta.warnings`.
`data` is shaped per call; read fields from the right place.

| Call | `data` | Per-item fields at |
| --- | --- | --- |
| `search`, `vocab` | flat list | each list element |
| `context` | `{query, items, total_chars, truncated}` | `items[].hit`, body at `items[].body` |
| `read` | one evidence read | `data.unit`, locator at `data.unit.locator` |
| `xlsx-read` | one range read | `data.cells[row][col]` |
| `answer` | one answer | `data.citations[]`, `data.refusal_reason` |

## Locators

| Source | `locator.type` | Carries |
| --- | --- | --- |
| PDF text | `pdf_page` | one-based `page` |
| PDF OCR | `pdf_ocr` | `page`, `bbox` |
| PDF table | `pdf_table` | page and table index |
| HWP/HWPX | `hwp_structure` | section, block, table, row, or cell when available |
| DOCX | `docx_paragraphs`, `docx_table`, `docx_textbox`, `docx_header_footer` | part or index |
| PPTX | `pptx_shape`, `pptx_notes`, `pptx_comment`, `pptx_part`, `pptx_ocr` | slide part or index |
| XLSX shallow unit | `xlsx_sheet` | a sheet only; never use it for a numeric claim |
| CSV | `csv_rows` | row range |
| Plain text | `text_line_range` | line range |
| Slack | `slack_message` | workspace, conversation, message timestamp, optional thread root |
| Mail | `email_message` | `account_id`, `mailbox`, `message_id`, `uid`; there is no MIME-part field |
| Any other connector event | `connector_object` | `connector` and `external_id` only; resolve the object in its own system |

`pdf_ocr` and `pptx_ocr` units are OCR output, not extracted text. Treat them as
lower-confidence evidence, label them as OCR when you quote them, and prefer a
`pdf_page` unit for the same page when one exists.

XLSX deep read has no locator type. `xlsx-read` returns the range itself —
`sheet`, `cell_range`, and the indexed vs. current source hash — so cite those.

## Freshness is three-valued

Decide from `source_verification`, never from `source_changed_since_index` alone.

| `source_verification` | Meaning | Claim this | Never claim this |
| --- | --- | --- | --- |
| `sha256` | the file was re-hashed now | equal hashes → current evidence; different hashes → the source changed after indexing | — |
| `stat` | size and mtime matched the index; no re-hash. Only `context` items and `answer` citations can carry it | current to a size/mtime check | that the bytes were re-hashed |
| `unavailable` | the source could not be read: cloud placeholder, unmounted, or permission denied. `source_changed_since_index` is `null` | freshness is **unknown**; the indexed text is still quotable as indexed text | that the source changed, was modified, was revised, or was deleted |

Which values a call can return is fixed. `read` always re-hashes the live
source, so it returns `sha256` or `unavailable`, never `stat`. `xlsx-read`
returns `sha256` and nothing else. `context` items and `answer` citations reopen
in bulk and so are the only places `stat` appears. `search` hits are always
`not_checked`.

An unverifiable source is never reported as a changed source. Say "the live
source could not be read, so I cannot verify it against the index" — never
"this document was modified after indexing".

`source_changed_since_index` is conclusive only when `source_verification` is
`sha256`. Under `stat` it is `false` by construction. Under `unavailable` it is
`null`: unknown, never fresh and never changed. Report `null` as unverified, and
never as "this document changed after indexing". An older deployment may still
send `true` there; `source_verification` decides either way.

`xlsx-read` always re-hashes and always reports `source_verification: sha256`,
because that path fails closed on an unreadable workbook. Its
`source_changed_since_index` is therefore always a real comparison, never `null`.

Search hits are not freshness evidence: they carry `evidence_role=discovery` and
`source_verification=not_checked`, and their hashes describe the index. Reopen
with `read` before any freshness statement.

Cite a locator as current only while `source_verification` is `sha256` or `stat`
and `source_changed_since_index` is `false`. Otherwise report the status and
withhold current-state conclusions. Re-index only on authorized maintenance.

## Refusals

`answer` refusing is a typed result, not an error, and never proof that no
matching document exists. Report what retrieval did find. Do not say "no
documents found" when your own `search` returned hits.

| `refusal_reason` | What it means | Next step |
| --- | --- | --- |
| `no_admissible_evidence` | nothing readable, permitted, and current matched — including a file named in the question that resolved to no indexed candidate | re-run `search` on the exact identifier or file name, `read` any hit, and answer from it; report scope or ACL limits, not absence |
| `no_fresh_evidence` | candidates existed but every reopen failed the freshness check | it carries no citations: re-run `search`, `read` a hit, quote it as indexed text, report its `source_verification`, and do not sync |
| `answer_not_present` | the document was found and admitted; the requested identifier, fact, or value is not stated in it | say the document was found but does not state it; it carries no citations, so re-run `search` and `read` a hit to confirm, or ask for the field name |
| `clarification_required` | several documents share the named filename, only exclusions were given, or the query matched several documents and is too general to choose between them | it carries no citations, and the generic case says so in its own message: run `search` on the same query to get the candidate list, then ask the user exactly one question naming those candidates |
| `exact_xlsx_read_required` | only shallow `xlsx_sheet` units matched | it carries citations: `xlsx-read` the cited artifact on the smallest sheet and range that covers the claim |
| `csv_full_table_required` | CSV evidence was partial, or the full table exceeds `max_chars` | there is no CSV read tool: `read` each cited unit until the table's rows are covered end to end. For the budget case, raise `max_chars` or read the cited units directly |
| `insufficient_decision_evidence` | an approval question whose evidence does not state completion. It is reachable only when generation is disabled or no generator is configured, **and** the query contains the literal Korean word `승인` | it carries citations: report approval as undetermined and quote what the cited units do state. Its absence never confirms approval — a generation-enabled deployment, or an approval question asked in English, never produces it |
| `model_egress_denied` | egress policy forbids sending this evidence to a model | answer extractively from `read`; do not retry and do not change configuration |
| `generation_unavailable` | no generator is configured, or the generator is unreachable | answer extractively from `read` and say generation was unavailable |
| `generation_invalid` | the generated claims failed citation validation | retry `answer` once, then answer extractively from `read` |

Only `exact_xlsx_read_required`, `csv_full_table_required` and
`insufficient_decision_evidence` carry `citations`; there they are the
next-step locators. Every other refusal is structurally citation-free, because
the refusal is built without citations and a citation can only be built from
evidence verified fresh. Never invent a locator for those: re-run `search` and
`read` a hit instead.

## Failed and degraded calls

| Signal | Meaning | Do this |
| --- | --- | --- |
| a `meta.warnings` entry ending in `_degraded` (`semantic_degraded`, `rerank_degraded`, `lexical_rerank_degraded`) | part of the ranking path was unavailable and it fell back | treat the result as weaker and mention it if it limits the answer; it does not authorize a sync or rebuild |
| `context_truncated` | the pack hit its character budget | narrow the query or `read` directly; a truncated pack cannot show that something is absent |
| `generation_unavailable_extractive_fallback` on a non-refused `answer` | the generator was unreachable and the answer was assembled extractively from the evidence instead | report it as an extractive answer, never as a generated one; the citations are still evidence |
| `generation_invalid_extractive_fallback` on a non-refused `answer` | the generated claims failed citation validation and the answer was assembled extractively instead | same: report it as extractive, and do not present the discarded generation |
| `ok: false` with `meta.warnings: ["search_failed"]` and `error.code: internal_error` | the search execution broke, usually `canceling statement due to statement timeout` (the database statement timeout defaults to 15s) | retry the same query, at most twice; if it still fails, narrow it — fewer terms, lower `limit`, an exact identifier — and retry once more |
| `ok: false` with `meta.warnings: ["search_failed"]` and `error.code: dependency_unavailable` or `source_unavailable` | a backend the search needs is down | retry with backoff; if it persists, report the deployment problem instead of retrying further |
| `ok: false` with **no** `search_failed` — `error.code` is `validation_error`, `forbidden`, `not_found` or `configuration_error` | the request or the deployment is wrong, and it will fail identically every time | do not retry: read `error.code`, fix the request, or report the deployment problem |

`search_failed` is the retryable marker, and it rides only a failure a retry
could resolve. Its absence on a failed search is information: a rejected query,
a denied scope, a missing ID or a misconfigured deployment carries no marker, so
retrying it is pointless. Either way the failure is not evidence that the corpus
lacks the term, and it never authorizes reading the database or index directly.
When retries are exhausted, tell the user the search backend failed and stop; do
not convert a failure into an absence claim.

## Cloud-only sources

`xlsx-read` failing with `not_found: source file is unavailable` means the file
is a cloud placeholder or its mount is gone: the bytes are not on disk and KIP
never downloads them. `search` and `read` still work, because the indexed text
was captured earlier. `answer` then usually refuses everything with
`no_fresh_evidence`, because every reopen fails the freshness check.

| Do | Do not |
| --- | --- |
| quote the indexed text from `read`, labelled as indexed text of unverified freshness | report exact cell values, totals, dates, or formulas — they are not obtainable |
| tell the user the file is cloud-only and that they must download it in the provider app (OneDrive or equivalent) before it can be read exactly | sync, re-index, hydrate, or open the placeholder yourself |
| run `./scripts/kip setup preview` to see `local_file_count` vs `cloud_placeholder_count` per source when asked how widespread this is — it reads the deployment's saved setup answers, fails when no source scope has been answered yet, and has no MCP equivalent | treat a deferred placeholder as a deleted or missing document |

This is the most common evidence dead end in a cloud-backed corpus. Say so early
instead of reporting the document as absent.

## `allow_stale`

`xlsx-read` verifies the workbook hash and fails with `conflict` when it changed
since indexing. `--allow-stale` (CLI), `allow_stale: true` (MCP) and
`?allow_stale=true` on `GET /v1/xlsx/{artifact_id}/range` (REST) skip that check
and read the file as it is now.

| Legitimate | Not legitimate |
| --- | --- |
| the user explicitly asks for the current on-disk values and accepts that they no longer match the index | clearing a `conflict` error to make a query succeed |
| comparing the changed original against the indexed extraction and reporting both | producing an index-backed citation from it |

It relaxes the evidence guarantee, so never use it silently. Always report that
you bypassed the freshness check, give both hashes, and check the response's
`source_changed_since_index`: when `true`, the values are current file values and
are not backed by the index. The response still carries
`source_verification: sha256` — always, because the path fails closed on an
unreadable workbook — so `allow_stale` only ever hides a known mismatch, never an
unknown.

## `is_latest` and supersession

`metadata.is_latest` on a search hit compares that hit's revision against the
newest indexed revision of the same logical document, and defaults to `true`
when there is nothing to compare.

| It does mean | It does not mean |
| --- | --- |
| this is, or ties, the newest indexed revision of this one document | this document is the current version of an agreement, policy, or contract |
| `false` → a newer revision of the same document exists | anything at all about any other document |
| `true` by default when no comparable revision timestamp exists | that no newer document exists |

`read` carries no supersession field. Cross-document supersession is assertable
only from an approved ontology assertion on the `supersedes` predicate, which is
`risk: high` and `review: required`. `graph neighbors --node-id ID` takes a
knowledge entity ID, not a document or unit ID — list candidates with
`ontology entities` first — then confirm the edge with `explain --assertion-id`
and read its evidence. Without an approved assertion, report supersession as undetermined and list the
candidate documents with their dates. Never infer it from filenames, dates,
`is_latest`, or search rank.

## `vocab` is a prefix lookup

`vocab PREFIX` (the MCP tool is named `kip_vocabulary`) returns indexed lexical
tokens that start with that prefix, with their document and corpus frequency. It is not synonym or alias expansion.

| It can | It cannot |
| --- | --- |
| confirm a term exists in the corpus and show its exact indexed spelling | expand a term to synonyms, translations, or related words |
| find longer forms and compounds sharing the prefix | find a different stem, an abbreviation, or a Korean/English equivalent |
| take one single term; a blank or multi-word prefix is rejected | take a phrase |

Use it to fix spelling and to check a term is present before reporting absence.
For alternatives, query each spelling yourself or traverse approved aliases.

## `xlsx-read` range cost

Each cell returns about 20 fields: value and cached value, both value types,
display value, data type, number format, date flag, both Excel serials, formula
with its kind, ref, and attributes, row and column hidden, row filtered, and
merge state. A sheet's whole used range copied from a search snippet can cost
tens of thousands of tokens and crowd out the answer.

Request the smallest range that covers the claim — the specific rows and columns,
never `A1:ZZ9999`. Widen only after a narrow read shows the values are elsewhere.
