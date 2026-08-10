# ADR-017: Native hwp-hwpx-parser is the HWP primary

Status: Accepted

## Decision

KIP uses the Python `hwp-hwpx-parser` reader as the first HWP/HWPX extraction path. The adapter emits bounded `hwp_native_chunk` evidence units, keeps source order and character offsets, and records table/image counts as extraction metadata. The existing command broker remains the fallback so Kordoc, unhwp, and paired PDFs can recover parser-specific failures.

## Evidence

On the actual OneDrive corpus, source-grounded text checks were run against HWP OLE preview text and HWPX section XML. The tested candidates were `rhwp-python`, `unhwp`, and `hwp-hwpx-parser`. In a same-service KIP A/B over real HWP/HWPX files, the native parser reached Recall@5 1.00 and MRR 1.00; `rhwp-python` reached 0.875 and 0.8125; the previous Kordoc path reached 0.444 and 0.444 and failed an ECV form.

These numbers are parser/retrieval evidence, not final answer quality. The semantic sidecar was rebuilt into a shadow space and lexical, hybrid, and reranked modes tied on the small internal set, so semantic activation is not implied by this decision.

## Consequences

- The extractor extra installs `hwp-hwpx-parser`.
- Native HWP units are chunked before embedding so a large document cannot exceed the model input limit as one unit.
- Existing source revisions adopt a parser upgrade through `parser reextract`
  shadow evaluation and the separate guarded `--activate` action. Activation
  keeps the prior extraction as recoverable history and changes one document
  atomically.
- Kordoc is disabled by default and must be installed before it is enabled;
  indexing never invokes a runtime package downloader. Kordoc, unhwp, and rhwp
  remain comparison candidates and should be rerun when their versions or the
  corpus change.
- The complete 2026-08-10 run parsed 86/86 HWP/HWPX files into 263 units with
  native parser provenance and unchanged source hashes. Its retrieval decision
  is recorded separately in ADR-031.
