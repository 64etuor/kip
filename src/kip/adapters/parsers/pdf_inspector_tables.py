from __future__ import annotations

from kip.adapters.parsers.pdf_tables import (
    PdfTableContext,
    PdfTableResult,
)
from kip.domain.models import ContentUnit, EvidenceLocator
from kip.domain.text import normalize_text, strip_inline_markup
from kip.ids import stable_id


def extract_markdown_table_units(
    markdown: str,
    page_index: int,
    context: PdfTableContext,
) -> PdfTableResult:
    page_number = page_index + 1
    ordinal = context.start_ordinal
    units: list[ContentUnit] = []
    for table_index, block in enumerate(_markdown_table_blocks(markdown)):
        columns = len(_cells(block[0]))
        rows = len(block) - 1
        if rows < 2 or columns < 2:
            continue
        body = "\n".join(block) + "\n"
        normalized = normalize_text(strip_inline_markup(body))
        units.append(
            ContentUnit(
                id=stable_id(
                    "unit",
                    context.extraction_id,
                    f"{page_index}-table-{table_index}",
                ),
                extraction_id=context.extraction_id,
                document_id=context.document_id,
                artifact_id=context.artifact_id,
                ordinal=ordinal,
                unit_type="pdf_table",
                title=f"table {table_index + 1} - page {page_number}",
                body=body,
                body_normalized=normalized,
                lexical_text=normalized,
                locator=EvidenceLocator(
                    type="pdf_table",
                    data={
                        "page": page_number,
                        "end_page": page_number,
                        "table_index": table_index,
                    },
                ),
                acl_scopes=list(context.acl_scopes),
                metadata={
                    "row_count": rows,
                    "col_count": columns,
                    "strategy": "markdown",
                    "source": "pdf_inspector",
                },
            )
        )
        ordinal += 1
    return PdfTableResult(tuple(units), ordinal)


def _markdown_table_blocks(markdown: str) -> tuple[tuple[str, ...], ...]:
    blocks: list[tuple[str, ...]] = []
    current: list[str] = []
    for raw_line in (*markdown.splitlines(), ""):
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            current.append(line)
            continue
        if len(current) >= 3 and _is_separator(current[1]):
            blocks.append(tuple(current))
        current = []
    return tuple(blocks)


def _is_separator(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(
        len(cell.strip().strip(":")) >= 3
        and set(cell.strip().strip(":")) == {"-"}
        for cell in cells
    )


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
