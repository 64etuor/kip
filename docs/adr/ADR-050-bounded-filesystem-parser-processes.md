# ADR-050: Bound filesystem parser processes

- **Status:** Accepted
- **Date:** 2026-08-16

## Context

PDF, DOCX, XLSX, PPTX, plain text, CSV, and native HWP parsing ran inside the
long-lived sync worker. A malformed or unusually expensive document could
therefore consume the worker's CPU or unified memory indefinitely. Existing
timeouts covered command-based HWP/OCR subprocesses only, not the Python parser
stack or all descendants. Captured child stdout also had no general response
size contract.

The production target is serial NAS indexing on an Apple M4 Pro with 24 GB of
unified memory. Normal search must remain independent from sync, re-extraction,
and embeddings. Parser tools must stay replaceable behind `ParserPort` rather
than leaking process concerns into application or domain code.

macOS exposes POSIX resource constants but does not accept a useful finite
`RLIMIT_AS`/`RLIMIT_DATA` reduction for this runtime. A portable design cannot
claim that those rlimits enforce memory on the reference host.

## Decision

1. Wrap every concrete filesystem parser at registry composition with
   `IsolatedParserAdapter`. Keep `ParserPort`, parser identity/version,
   `supports()`, and returned domain models unchanged.
2. Run one document in a fresh Python child. The child reconstructs only the
   selected raw adapter and receives a minimal, allowlisted environment with
   native math/OCR thread counts pinned.
3. Exchange `kip.parser-request.v1` and `kip.parser-response.v1` through files
   in a private temporary directory. Do not carry parser payloads through
   stdout/stderr pipes. Validate the typed response and reject it before read
   when it exceeds the configured result budget.
4. Let the parent supervise wall time and aggregate RSS for the child plus all
   descendants every 100 ms. Kill the POSIX process group on timeout or memory
   excess. Apply `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_NOFILE`, disabled core
   dumps, and `nice` in every POSIX child. On Linux, add `RLIMIT_AS` and
   `RLIMIT_DATA` as defense in depth.
5. Use the following M4 Pro 24 GB reference defaults:

   | Limit | Default |
   |---|---:|
   | Concurrency | serial, one document |
   | Native-library threads | 4 |
   | Aggregate process-tree RSS | 6144 MiB |
   | CPU time | 120 s |
   | Wall time | 180 s |
   | Response file | 256 MiB |
   | Retained diagnostic tail | 16 KiB |
   | Child nice value | 5 |

6. Convert timeout, memory excess, invalid/oversized response, and abnormal
   child termination into a stable per-file `ParserError`. Existing ingestion
   isolation then continues the sync and preserves the previous active
   extraction.
7. Leave embedding selection, corpus coverage, and projection activation
   unchanged. This decision concerns extraction resource containment only.

## Measured evidence

Fresh registry startup probes took 0.14-0.19 s and about 44 MB maximum RSS.
Raw-versus-isolated parsing on seven anonymized, locally allocated OneDrive/NAS
format samples produced equivalent extraction/unit contracts and unchanged
source SHA-256 values. Small Office/HWP samples added about 0.18-0.23 s fixed
overhead. The measured PDF sample added 1.86 s while running table detection;
the PPTX run varied in the opposite direction due to cache/runtime variance.
Maximum observed isolated RSS was about 1.15 GB on the PPTX sample, leaving
roughly fivefold headroom under the 6 GiB default. These measurements establish
host feasibility, not broad corpus quality or OCR accuracy.

A supplemental large-local-file pass selected the largest locally allocated
sample at or below 64 MiB for PDF, PPTX, and XLSX: 53,472,677-byte PDF in
1.840 s (231 units, succeeded), 57,064,480-byte PPTX in 0.354 s (94 units,
partial with one warning), and 63,216,202-byte XLSX in 0.236 s (2 units,
succeeded). All three source hashes were unchanged; the combined driver's
maximum resident set was 164,380,672 bytes.

Contract tests also prove wall timeout and descendant teardown, 96 MiB RSS
enforcement against a 256 MiB allocation, response-size rejection, POSIX CPU/
file/descriptor limits, typed child errors without source-path leakage, and
raw/isolated result equivalence.

An independent scoped Luna rerun on the final parser/config implementation used
10 locally allocated anonymized OneDrive samples across PDF, PPTX, XLSX, XLSM,
HWP, HWPX, and DOCX. Raw and isolated outputs matched in extraction status,
unit bodies/counts, locators, ordinals, and generic warning categories; the
aggregate status was 8 succeeded, 2 partial, and 0 failed on both paths. All
source SHA-256 values were unchanged. The largest process-tree RSS in this
sample was 162,529,280 bytes (155 MiB), and 94 focused parser/supervisor tests
passed. Synthetic wall-time, descendant cleanup, RSS, response-size,
diagnostic-tail, malformed-input, and temp cleanup probes also passed. This is
resource-containment and extraction-contract evidence, not OCR semantic,
spreadsheet deep-read, placeholder, or full-corpus certification.

## Consequences

- A parser crash, hang, runaway descendant, or memory spike no longer shares
  the long-lived worker's resource lifetime.
- Per-document startup is a deliberate fixed cost. Serial execution avoids
  memory-pressure amplification on the 24 GB reference host; concurrency can
  be reconsidered only after corpus-level peak-RSS measurement.
- Child results can be large but are bounded on disk and before parent read.
- `psutil` becomes a core runtime dependency so macOS can supervise the full
  descendant tree rather than only the direct child.
- Resource isolation does not make a writable NAS mount read-only and does not
  create a macOS network namespace. Production must continue mounting sources
  read-only and enforcing network policy in the outer container/launcher.
- Python parser imports occur per document. If future measurements show startup
  dominating ingestion, a bounded recyclable worker pool may replace the
  one-shot child without changing `ParserPort` or the request/response schema.

## References

- `src/kip/adapters/parsers/isolation.py`
- `src/kip/adapters/parsers/process_supervisor.py`
- `src/kip/adapters/parsers/isolated_worker.py`
- `src/kip/adapters/parsers/registry.py`
- `tests/test_parser_isolation.py`
- AGENTS.md rules 2, 5, 12, and 13
