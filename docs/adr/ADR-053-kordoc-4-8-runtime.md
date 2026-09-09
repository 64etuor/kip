# ADR-053: Upgrade the pinned offline Kordoc runtime to 4.8.0

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

KIP pinned Kordoc 4.7.3 for HWP/HWPX fallback parsing and PP-OCRv5 Korean
candidate extraction. Kordoc 4.8.0 adds a `pages` projection containing
per-page Markdown to the existing JSON result. It does not remove the blocks,
page count, metadata, or warning fields KIP consumes, but it can increase JSON
output size and therefore requires measurement rather than a blind pin change.

The upstream contract uses `metadata.pageMode="layout"` for layout-cache-backed
physical pages and `"section"` for section approximations. KIP previously
recognized only the literal `"exact"`, so a Kordoc layout page would have been
mislabeled `section_approx` at the evidence boundary.

## Measured evidence

- The npm package is version 4.8.0, requires Node 18+, and retains the Kordoc
  CLI entry point and dependency surface used by KIP.
- Six checked-in public PDFs produced identical blocks, page counts, metadata,
  and warning-code sequences under 4.7.3 and 4.8.0: 1,166 blocks in total.
  The additive pages projection increased exact JSON byte size by 1.79-7.86%
  on this image-heavy set.
- Two public upstream HWPX fixtures produced identical blocks, page counts,
  metadata, and `pageMode="section"`; JSON size increased by 9.79% and 20.96%.
- The installed 4.8.0 CLI version and existing PP-OCRv5 Korean model hashes
  passed `install-kordoc.sh` and `doctor.sh` with the new reference config.
- The package's ordinary consumer install resolved vulnerable nested
  `adm-zip` and `sharp` versions. A clean isolated root with `adm-zip` 0.6.0
  and `sharp` 0.35.3 overrides retained Kordoc 4.8.0 behavior and reported
  zero npm audit vulnerabilities.
- Focused Kordoc, OCR, setup, doctor, PPTX, upstream-watch, and HWP broker tests
  passed. Full repository and extracted-starter verification remain required
  release gates.

## Decision

1. Pin Kordoc 4.8.0 in the local installer, production image, reference
   configurations, setup output, adapter defaults, doctor, and documentation.
2. Continue consuming Kordoc blocks and metadata. Do not store or expose the
   additive `pages` Markdown projection as a new KIP contract.
3. Map Kordoc `pageMode="layout"` to KIP `page_mode="exact"`; map `"section"`
   and absent or unknown modes to the conservative `section_approx` value when
   a page number exists. Preserve `null` when no page number exists.
4. Keep exact version probing, the preinstalled launcher, model prewarming,
   `KORDOC_OFFLINE=1`, and rejection of runtime `npm`/`npx` execution.
5. Supersede only ADR-042's concrete 4.7.3 runtime pin. Its OCR routing,
   evidence, offline, review, and quality requirements remain in force.
6. Build local and container installs in an isolated root that overrides
   `adm-zip` to 0.6.0 and `sharp` to 0.35.3. Increment the runtime revision
   when those security pins change so a stale nested tree cannot be reused.

## Consequences

- New starter installations use the current Kordoc release while preserving
  KIP evidence-unit and warning semantics on the measured public corpus.
- Per-page Markdown increases subprocess output and transient memory. The
  measured public files remain far below the parser-worker limits, but this is
  not a full-corpus memory or OCR-accuracy certification.
- No new page claim is made for HWPX files that report `pageMode="section"`.
  Exact labeling is allowed only when Kordoc explicitly reports `"layout"`.
- The source starter contains only install policy. Kordoc code and OCR models
  remain online bootstrap/image-build inputs and are excluded from the ZIP.
- Historical 4.7.3 OCR-quality measurements remain historical evidence; a
  future model or OCR-output change still requires a fresh quality evaluation.

## References

- Kordoc commit `f8a97098e49e` and npm package `kordoc@4.8.0`
- `src/kip/adapters/ocr/kordoc.py`
- `src/kip/adapters/parsers/hwp_broker.py`
- `docs/CONNECTORS.md`
- `docs/SECURITY.md`
