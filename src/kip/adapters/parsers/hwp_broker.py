from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kip.application.analyzer import normalize_text
from kip.domain.models import ContentUnit, EvidenceLocator, ExtractionRun
from kip.errors import ParserError
from kip.ids import new_id, sha256_bytes, stable_id


@dataclass(slots=True)
class CommandParserConfig:
    name: str
    argv: list[str]
    enabled: bool = True
    timeout_seconds: int = 120


class HwpParserBroker:
    name = "hwp-broker"
    version = "1.0"

    def __init__(self, configs: list[CommandParserConfig], paired_pdf_parser: Any | None = None) -> None:
        self.configs = [config for config in configs if config.enabled]
        self.paired_pdf_parser = paired_pdf_parser

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".hwp", ".hwpx"}

    def parse(self, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        failures: list[str] = []
        successful: list[tuple[float, ExtractionRun, list[ContentUnit]]] = []
        for config in self.configs:
            if not self._command_exists(config.argv[0]):
                failures.append(f"{config.name}: command unavailable")
                continue
            try:
                extraction, units = self._run_command_parser(
                    config, path, artifact_id=artifact_id, document_id=document_id, acl_scopes=acl_scopes
                )
                successful.append((extraction.quality_score or 0.0, extraction, units))
            except Exception as exc:
                failures.append(f"{config.name}: {exc}")

        if successful:
            successful.sort(key=lambda item: item[0], reverse=True)
            _, extraction, units = successful[0]
            extraction.warnings.extend(failures)
            extraction.metadata["broker_candidates"] = [item[1].parser_name for item in successful]
            return extraction, units

        paired_pdf = path.with_suffix(".pdf")
        if self.paired_pdf_parser and paired_pdf.exists():
            extraction, units = self.paired_pdf_parser.parse(
                paired_pdf, artifact_id=artifact_id, document_id=document_id, acl_scopes=acl_scopes
            )
            extraction.parser_name = "hwp-paired-pdf-fallback"
            extraction.warnings.extend(failures)
            extraction.warnings.append(f"parsed paired PDF instead of {path.name}")
            return extraction, units
        raise ParserError("all HWP parsers failed: " + "; ".join(failures))

    @staticmethod
    def _command_exists(command: str) -> bool:
        if "/" in command:
            return Path(command).exists()
        return shutil.which(command) is not None

    def _run_command_parser(self, config: CommandParserConfig, path: Path, *, artifact_id: str, document_id: str, acl_scopes: list[str]) -> tuple[ExtractionRun, list[ContentUnit]]:
        with tempfile.TemporaryDirectory(prefix="kip-hwp-") as temp:
            output_dir = Path(temp)
            argv = [
                item.replace("{input}", str(path)).replace("{output_dir}", str(output_dir))
                for item in config.argv
            ]
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
            )
            if completed.returncode != 0:
                stderr = completed.stderr[-2000:].replace("\x00", " ")
                raise ParserError(f"exit {completed.returncode}: {stderr}")
            payload = self._load_payload(completed.stdout, output_dir)
            metadata = payload.get("metadata") or {}
            blocks = payload.get("blocks") or payload.get("units") or []
            markdown = payload.get("markdown") or payload.get("text") or ""
            if not blocks and markdown:
                blocks = [{"type": "document", "text": markdown}]
            if not blocks:
                raise ParserError("parser returned no text blocks")
            extraction_id = new_id("ext")
            units: list[ContentUnit] = []
            total_text = ""
            for ordinal, block in enumerate(blocks):
                text = str(block.get("text") or block.get("markdown") or block.get("content") or "")
                if not text.strip() and block.get("rows"):
                    text = json.dumps(block.get("rows"), ensure_ascii=False)
                if not text.strip():
                    continue
                total_text += text + "\n"
                normalized = normalize_text(text)
                locator_data = {
                    "section": block.get("section") or block.get("sectionNumber"),
                    "page": block.get("pageNumber"),
                    "block": ordinal,
                    "bbox": block.get("bbox"),
                }
                units.append(
                    ContentUnit(
                        id=stable_id("unit", extraction_id, str(ordinal)),
                        extraction_id=extraction_id,
                        document_id=document_id,
                        artifact_id=artifact_id,
                        ordinal=ordinal,
                        unit_type=str(block.get("type") or "hwp_block"),
                        title=str(block.get("heading") or path.name),
                        body=text,
                        body_normalized=normalized,
                        lexical_text=normalized,
                        locator=EvidenceLocator(type="hwp_structure", data=locator_data),
                        acl_scopes=acl_scopes,
                        metadata={"style": block.get("style"), "href": block.get("href")},
                    )
                )
            if not units:
                raise ParserError("parser output contained no usable text")
            quality = self._quality(total_text, payload)
            extraction = ExtractionRun(
                id=extraction_id,
                artifact_id=artifact_id,
                parser_name=config.name,
                parser_version=str(metadata.get("parserVersion") or payload.get("version") or "unknown"),
                status="partial" if payload.get("warnings") else "succeeded",
                quality_score=quality,
                output_hash=sha256_bytes(total_text.encode("utf-8")),
                warnings=[str(item) for item in payload.get("warnings", [])],
                metadata={"document_metadata": metadata},
            )
            return extraction, units

    @staticmethod
    def _load_payload(stdout: str, output_dir: Path) -> dict[str, Any]:
        value = stdout.strip()
        if value:
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        candidates = [
            output_dir / "content.json",
            output_dir / "extract.json",
            output_dir / "extract.md",
            output_dir / "extract.txt",
        ]
        candidates.extend(output_dir.rglob("content.json"))
        candidates.extend(output_dir.rglob("extract.md"))
        candidates.extend(output_dir.rglob("extract.txt"))
        for candidate in candidates:
            if not candidate.exists() or not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if candidate.suffix == ".json":
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            return {"markdown": text}
        if value:
            return {"markdown": value}
        raise ParserError("no parser output found")

    @staticmethod
    def _quality(text: str, payload: dict[str, Any]) -> float:
        if not text:
            return 0.0
        hangul = sum(1 for char in text if "가" <= char <= "힣")
        printable = sum(1 for char in text if char.isprintable() or char in "\n\t")
        base = min(1.0, len(text) / 2000)
        hangul_ratio = hangul / max(1, len(text))
        printable_ratio = printable / max(1, len(text))
        warning_penalty = min(0.3, len(payload.get("warnings", [])) * 0.03)
        return max(0.0, min(1.0, 0.4 * base + 0.3 * printable_ratio + 0.3 * min(1.0, hangul_ratio * 3) - warning_penalty))
