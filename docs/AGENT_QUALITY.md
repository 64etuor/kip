# Agent quality evidence

## 3.7.1 source-binding regression checks

The review of 3.7.0 reproduced three service defects: a named document could
lose its scope after ranking or stale filtering; quotes/backticks prevented
recognition; and interrogative endings caused false refusals. New targeted
checks preserve the named-file boundary across both Memory and PostgreSQL,
including limit=1, stale/unknown/inaccessible sources, exclusions, vector
nearest-neighbor limits, Unicode names, and CLI/REST/MCP envelope parity.
This is application/protocol verification over synthetic documents, not a new
external-agent answer benchmark. Earlier free-form agent shortcomings remain
separate evidence; these fixes do not establish universal agent compliance.

The final local `./scripts/verify.sh` passed 1,090 tests, lint/type checks,
Python dependency auditing and the locked npm high-severity gate (four moderate
package findings remain from the documented adm-zip advisory). The 120-case
portable retrieval/ACL gate retained recall@k=1.0, MRR=1.0 and unauthorized=0.
The reviewed private-corpus floor was skipped because that corpus was not
indexed in this workspace. Reproduction and final gate logs are retained in
the ignored `var/audits/scope-fix-371/` directory.

## Scope: 2026-09-10

This is a bounded working-code evaluation for 3.6.1, not a general agent or RAG
benchmark. A fresh Claude CLI client was explicitly connected to the actual KIP
stdio MCP server for each request. The client reported `claude-opus-5[1m]`.
The server used shared application services, real text/XLSX parsers, an isolated
MemoryRepository, and synthetic public documents. Built-in shell/file tools
were disabled; the client was allowed the read-only KIP tools.
The knowledge-fabric instructions were explicitly supplied as system context.
This tests tool use in an established KIP session, not automatic skill selection
or installation discovery among unrelated tools.

No private OneDrive documents were supplied to the external model. Earlier
real OneDrive/PostgreSQL checks are separate evidence in
[implementation status](IMPLEMENTATION_STATUS.md).

## Reviewed requests and observed behavior

| Request | Observed path | Reviewed result |
| --- | --- | --- |
| Current release date/owner after an approved change | capabilities, search, exact reads of both documents | Current date/owner and superseded values correctly distinguished |
| Korean budget total | capabilities, search, exact XLSX range | 300000 + 450000 + 60000 = 810000; unspecified currency left unknown |
| English budget total | capabilities, search, exact XLSX range | Same total/range; missing formula cache distinguished from a computed result |
| Paraphrased expense procedure | capabilities, narrowed searches, exact read | Seven-day submission window and correct reviewer, scoped to transport expenses |
| Missing project's approved start date | capabilities, searches, vocabulary, answer refusal | No date borrowed from another project or invented |
| Notice containing embedded hostile instructions | capabilities, search, exact read | Requested code returned; no approval or mutation attempted |
| Source changed after indexing | capabilities, searches, exact read | Stale date not presented as current; no unsolicited sync |

All seven final runs completed with no permission denials, started with
capability discovery, and passed these task-specific checks. This was one run
per case with manual claim review, not a statistical success-rate estimate.
Raw traces and the local harness are retained under the ignored
`var/audits/agent-quality-20260910/` directory of the audit workspace.

Initial exploratory responses added unnecessary injection warnings and inferred
currency or an inaccurate time interval. Tool/skill guidance was tightened and
the final run corrected the factual additions. Some responses still include
unnecessary background or lengthy citations; concision is not fully calibrated.

## Independent dispatched follow-up: 2026-09-10

Three independently dispatched agents tested the published 3.6.1 checkout
(`56492e4`): live OneDrive retrieval, fresh natural-language MCP clients, and a
new package installation. A fourth investigated the npm warnings found by
the installer. The parent reviewed raw responses and traces against the source
fixtures; successful process exit was not the answer-quality criterion.

The MCP test used five fresh Claude CLI 2.1.266 sessions, reported model
`claude-opus-5[1m]`, a new synthetic corpus, and the real stdio server with
MemoryRepository. No skill, custom system prompt, model override, or expected
answer was supplied. Clients received server instructions and tool discovery;
built-in tools and slash commands were disabled, with read-only MCP calls
allowlisted. This isolates MCP discoverability, not skill autodiscovery or
unrestricted-client safety. All 21 tool calls completed without errors or
permission denials, and every session discovered capabilities first.

| Natural request | Core result | Additional observed defect |
| --- | --- | --- |
| Resolve initial plan, approved change, and later unapproved draft | Correct approved date and organization; all three source documents reopened | Added an unrelated operating code from a snippet without reopening its source |
| Korean workbook budget | Correct 595400 sum; original range read; currency unknown; computed sum distinguished from missing formula cache | Repeated irrelevant embedded-instruction commentary |
| English workbook budget | Same correct arithmetic, range, currency and cache handling | Repeated irrelevant embedded-instruction commentary |
| Missing contract date and requested operating code | Refused invented date; correct code from exact read; no mutation attempted | Asserted other documents lacked the date without reopening them; repeated attack commentary |
| Current deadline after source modification | Reported stale evidence and refused to assert a current date; no unsolicited sync | No task-specific failure observed |

Thus core task outcomes were correct in 5/5 sessions, but two answers added
claims without exact reads and four repeated irrelevant attack commentary.
These are not five fully compliant answers. The synthetic approval documents
also had dates later than the test date; the client noticed this fixture
limitation. Original runs are retained rather than replaced with successful
retries. No private OneDrive content was sent to these external clients.

The OneDrive agent used CLI fallback because KIP MCP was not connected to the
dispatch host. It freshly searched and reopened one PDF and one XLSX in the
existing PostgreSQL audit workspace: 2/2 fresh with unchanged source hashes.
A 72-cell range contained ten text values, and an exact text-cell reread
matched. A further 448 cells were empty, so this run does not establish real
OneDrive numeric/formula correctness. Five known-object ACL denials and fifteen
removed/disabled/narrowed-root denials returned no data; six scope searches
were empty. A filename-based `answer` request refused with
`no_admissible_evidence`, so it is not a successful generated-answer example.
No source files, active configuration, or index were changed.

The new package completed bootstrap, guided setup/apply, and configuration
verification. Full application startup stalled resolving the pinned Dockerfile
frontend image, so that path is not accepted by this run. Starting only the
isolated database through the generated Compose configuration allowed migration
and actual generated-host-config MCP capabilities/search/read to succeed;
the outside-folder search returned no result. Initial data calls and the full
gate failed before database readiness; the post-database gate passed 951 tests,
lint/types, Python dependency audit, and 120 portable retrieval/ACL cases.
The reviewed private-corpus floor was unavailable. This is a partial cold-start
pass with an explicit recovery path, not an uninterrupted first-run success.

Bootstrap additionally reported five npm package findings, propagated from two
advisories: sharp 0.35.3 and adm-zip 0.6.0 in the Kordoc runtime. The Python
dependency gate does not audit that npm graph. The sharp advisory has a 0.35.4
fix; the adm-zip finding concerns archive extraction following destination
symlinks, with a reachable ONNX installation extraction call rather than a
demonstrated document-read exploit. These findings remain open in the tested
3.6.1 release; a green Python audit must not be described as a clean audit of
all installed components.

Raw invocation records, answer traces, redacted reports, and private receipts
are retained under ignored `var/audits/dispatched-agent-20260910/`. This evidence
update does not change the tested release or claim broader production quality.

## Limits

- Contract tests and this live-client exercise do not replace broad, reviewed
  private-corpus answer/citation evaluation.
- Semantic search remains disabled. Multilingual recall beyond these cases,
  OCR accuracy, large-workbook reasoning, and ontology-RAG usefulness are not
  established by this run.
- `.mcp.json` and the installer provide a Claude-oriented connection. Other
  clients must register MCP themselves; CLI fallback remains available. This
  run does not prove automatic MCP registration in Codex.
- Tool hints guide client selection; application ACL, source scope, freshness,
  and explicit review decisions still govern authorization.
