from __future__ import annotations

from dataclasses import dataclass

from kip.domain.models import ContentUnit, EvidenceLocator
from kip.domain.text import normalize_text
from kip.ids import stable_id
from kip.ports.ocr import OcrDocument


@dataclass(frozen=True, slots=True)
class PdfOcrContext:
    extraction_id: str
    artifact_id: str
    document_id: str
    acl_scopes: tuple[str, ...]
    ordinal_start: int
    candidate_pages: frozenset[int]


def pdf_ocr_units(
    documents: tuple[OcrDocument, ...],
    context: PdfOcrContext,
) -> list[ContentUnit]:
    units: list[ContentUnit] = []
    for document in documents:
        for block in document.blocks:
            if block.page not in context.candidate_pages or not block.text.strip():
                continue
            ordinal = context.ordinal_start + len(units)
            normalized = normalize_text(block.text)
            units.append(
                ContentUnit(
                    id=stable_id("unit", context.extraction_id, str(ordinal)),
                    extraction_id=context.extraction_id,
                    document_id=context.document_id,
                    artifact_id=context.artifact_id,
                    ordinal=ordinal,
                    unit_type="pdf_ocr",
                    title=f"{document.source_path.name} - page {block.page or 1} OCR",
                    body=block.text,
                    body_normalized=normalized,
                    lexical_text=normalized,
                    locator=EvidenceLocator(
                        type="pdf_ocr",
                        data={"page": block.page or 1, "bbox": block.bbox},
                    ),
                    acl_scopes=list(context.acl_scopes),
                    metadata={"block_type": block.block_type, **block.metadata},
                )
            )
    return units


def page_was_ocrd(warning: str, covered_pages: set[int | None]) -> bool:
    if not warning.startswith("page "):
        return False
    page_text = warning.removeprefix("page ").split(":", maxsplit=1)[0]
    return page_text.isdigit() and int(page_text) in covered_pages


def pdf_ocr_reason(text: str) -> str | None:
    characters = [character for character in text if character not in " \t\n\r"]
    total = len(characters)
    if total < 20:
        return "low_text"
    pua = sum(
        "\ue000" <= character <= "\uf8ff"
        or "\U000f0000" <= character <= "\U000ffffd"
        or "\U00100000" <= character <= "\U0010fffd"
        for character in characters
    )
    control = sum(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or 0x80 <= ord(character) <= 0x9F
        for character in characters
    )
    replacement = characters.count("\ufffd")
    if pua / total >= 0.2:
        return "high_pua"
    if control / total >= 0.05:
        return "high_control"
    if replacement / total >= 0.05:
        return "high_replacement"
    return None
