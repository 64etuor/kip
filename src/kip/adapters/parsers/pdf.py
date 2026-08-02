from __future__ import annotations

from pathlib import Path

from kip.application.analyzer import normalize_text
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.errors import DependencyUnavailableError, ParserError
from kip.ids import new_id, sha256_bytes, stable_id


class PdfParser:
    name = "pymupdf"
    version = "1"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        try:
            import fitz
        except ImportError as exc:
            raise DependencyUnavailableError("Install the extractors extra for PDF parsing") from exc
        extraction_id = new_id("ext")
        units: list[ContentUnit] = []
        warnings: list[str] = []
        try:
            document = fitz.open(path)
            for index, page in enumerate(document):
                text = page.get_text("text") or ""
                normalized = normalize_text(text)
                if len(normalized) < 20:
                    warnings.append(f"page {index + 1}: low text; OCR may be required")
                units.append(
                    ContentUnit(
                        id=stable_id("unit", extraction_id, str(index)),
                        extraction_id=extraction_id,
                        document_id=document_id,
                        artifact_id=artifact_id,
                        ordinal=index,
                        unit_type="pdf_page",
                        title=f"{path.name} - page {index + 1}",
                        body=text,
                        body_normalized=normalized,
                        lexical_text=normalized,
                        locator=EvidenceLocator(type="pdf_page", data={"page": index + 1}),
                        acl_scopes=acl_scopes,
                        metadata={"page": index + 1},
                    )
                )
        except Exception as exc:
            raise ParserError(f"PDF parse failed: {path}: {exc}") from exc
        body = "\n".join(unit.body for unit in units)
        status = "partial" if warnings else "succeeded"
        quality = 0.6 if warnings and not body.strip() else 0.95
        extraction = ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name=self.name,
            parser_version=self.version,
            status=status,
            quality_score=quality,
            output_hash=sha256_bytes(body.encode("utf-8")),
            warnings=warnings,
        )
        return extraction, units
