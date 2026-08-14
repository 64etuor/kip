# ADR-041: Structured PPTX extraction preserves presentation evidence

- **Status:** Accepted
- **Date:** 2026-08-13

## Context

KIP declared PPTX as a source format but had no active parser. Treating a deck
as a flat slide string would lose merged tables, chart caches, image identity,
speaker notes, nested groups, comments, SmartArt, geometry, and the distinction
between source z-order and a derived reading order. The SolarEdge corpus also
contains materially newer project evidence in PPTX files than in some indexed
workbooks, so omission is a retrieval-coverage problem rather than a display
feature.

## Decision

1. Use pinned `python-pptx` as the object-model adapter and a bounded read-only
   OOXML scan for package features the library does not expose consistently.
2. Emit typed shape-level content units for text, tables, charts, images,
   speaker notes, legacy comments, and SmartArt data.
3. Preserve slide/shape IDs, nested group path, EMU geometry, source z-order,
   and geometry-derived reading order. Preserve table merge ownership, chart
   cached values, text runs/hyperlinks, and image metadata plus SHA-256.
4. Keep valid primary slide content when an optional comment or diagram part is
   malformed, mark the extraction `partial`, and retain a typed warning.
5. Never execute macros, fetch external relationships, recalculate linked data,
   or expand embedded OLE/package objects. Enforce ZIP entry, expanded-size,
   and compression-ratio limits before constructing the presentation model.
6. Keep OCR, media transcription, modern threaded comments, embedded-object
   expansion, and legacy binary `.ppt` as separate future adapters.

## Consequences

- Normal filesystem sync and every retrieval edge receive the same structured
  PPTX units through the existing application service and registry.
- Presentation evidence is searchable without pretending that derived reading
  order is source z-order or that an image placeholder is OCR text.
- Optional-part damage is observable without discarding unrelated slide
  evidence; embedded objects require follow-up instead of silent omission.
- `python-pptx`, Pillow, and XlsxWriter enter the locked extractor dependency
  graph. Upgrades require contract fixtures and real-corpus comparison.

## References

- `docs/PRD.md`
- `docs/TRD.md`
- `docs/DATA_CONTRACTS.md`
- `docs/SECURITY.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/PRODUCTION_DESIGN_ALIGNMENT.md`
