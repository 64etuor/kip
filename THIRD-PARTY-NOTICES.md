# Third-Party Notices

KIP Knowledge Fabric is licensed under the MIT License; see `LICENSE`.

This file lists the third-party components KIP declares as dependencies and
the license each one is distributed under. The authoritative, machine-readable
inventory is the SPDX SBOM produced by `make release`; this document is the
human-readable summary and can drift, so verify against the SBOM for a specific
release.

## Copyleft components — read before you distribute

Two declared dependencies are copyleft. KIP does not relicense them, and
shipping KIP does not place KIP's own MIT terms on them.

### PyMuPDF — GNU AGPL-3.0 (or a commercial Artifex license)

`PyMuPDF` is distributed as "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex".
It reaches a deployment through two paths:

- it is pinned in `requirements/runtime.txt`, which the `Dockerfile` installs,
  so the production image contains it;
- the default PDF backend calls it. `pdf-inspector` (MIT) is the starter
  default, but table pages without structured Markdown fall back to PyMuPDF
  `find_tables(lines_strict)` (`src/kip/adapters/parsers/pdf_tables.py`,
  ADR-054).

AGPL-3.0 is network copyleft: offering a service built on AGPL code to users
over a network carries source-offer obligations for that component. If that is
incompatible with your deployment, either obtain an Artifex commercial license
or remove the component — set `[parsers.pdf] tables_enabled = false`, drop the
`extractors` extra, and rebuild the runtime lock without `pymupdf`. Confirm the
result with your own counsel; this file is a factual inventory, not legal
advice.

### psycopg and psycopg-pool — LGPL-3.0-only

Used through their public Python API without modification. LGPL-3.0 is weak
copyleft and does not extend to KIP, but redistributing them carries the
LGPL's own notice and relinking obligations.

## Python dependencies

### Base (`pyproject.toml` `[project] dependencies`)

| Component | Version | License |
| --- | --- | --- |
| pydantic | 2.13.4 | MIT |
| psutil | 7.2.2 | BSD-3-Clause |
| typer | 0.27.1 | MIT |
| PyYAML | 6.0.3 | MIT |
| tomli-w | 1.2.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| rapidfuzz | 3.14.5 | MIT |

### Optional extras

| Component | Version | License | Extra |
| --- | --- | --- | --- |
| psycopg | 3.3.4 | **LGPL-3.0-only** | `postgres` |
| psycopg-pool | 3.3.1 | **LGPL-3.0-only** | `postgres` |
| fastapi | 0.141.1 | MIT | `api` |
| uvicorn | 0.52.3 | BSD-3-Clause | `api` |
| PyJWT | 2.13.0 | MIT | `identity` |
| openpyxl | 3.1.5 | MIT | `extractors` |
| pdf-inspector | 1.14.2 | MIT | `extractors` |
| PyMuPDF | 1.28.2 | **AGPL-3.0 or Artifex commercial** | `extractors` |
| python-pptx | 1.0.2 | MIT | `extractors` |
| hwp-hwpx-parser | 1.0.0 | Apache-2.0 | `extractors` |
| torch | 2.13.0 | Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | `semantic` |
| transformers | 5.15.0 | Apache-2.0 | `semantic` |
| einops | 0.8.2 | MIT | `semantic` |
| mcp | 2.0.0 | MIT | `mcp` |
| opentelemetry-* | 1.30+ | Apache-2.0 | `telemetry` |

Development-only dependencies (`dev` extra: pytest, ruff, mypy, pip-audit and
their type stubs) are not distributed in the runtime image.

## Non-Python components

| Component | Version | License | How it is used |
| --- | --- | --- | --- |
| Kordoc | 4.8.0 | MIT | Pinned offline OCR/document runtime installed under `var/` by `./scripts/install-kordoc.sh`; never vendored into this repository (ADR-053) |
| PostgreSQL | 18 | PostgreSQL License | Database engine, via the container image |
| pgvector | 0.8.2 | PostgreSQL License | Vector index extension, via `pgvector/pgvector:0.8.2-pg18-trixie` |

Container base images carry the licenses of their own contents; the image
digest recorded in `deploy/production.env` identifies exactly what was shipped.

## Corpora and sample data

`sample-data/` and the public evaluation corpus are covered by their own terms,
recorded in `evaluation/` alongside each dataset. No private customer corpus is
included in any released artifact; `scripts/release_artifacts.py` and the
starter archive policy fail the build if one is detected.
