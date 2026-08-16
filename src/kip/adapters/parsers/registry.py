from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from kip.adapters.ocr.kordoc import KordocOcrAdapter, KordocOcrConfig
from kip.adapters.parsers.csv_table import CsvTableParser
from kip.adapters.parsers.docx import DocxParser
from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker
from kip.adapters.parsers.hwp_native import HwpNativeParser, HwpParserChain
from kip.adapters.parsers.isolation import IsolatedParserAdapter
from kip.adapters.parsers.pdf import PdfParser
from kip.adapters.parsers.plain import PlainTextParser
from kip.adapters.parsers.pptx import PptxParser
from kip.adapters.parsers.pptx_ocr import PptxOcrLimits
from kip.adapters.parsers.process_supervisor import ParserIsolationLimits
from kip.adapters.parsers.xlsx import XlsxShallowParser
from kip.domain.json_types import JsonObject
from kip.errors import ConfigurationError, ParserError
from kip.ports.parser import ParserPort
from kip.settings import Settings

_REPRESENTATION_ROLE_BY_EXTENSION: dict[str, str] = {
    ".hwp": "editable_original",
    ".hwpx": "editable_original",
    ".pdf": "searchable_representation",
    ".xlsx": "workbook",
    ".xlsm": "workbook",
    ".xls": "workbook",
    ".csv": "workbook",
    ".pptx": "presentation",
    ".pptm": "presentation",
    ".ppsx": "presentation",
    ".ppsm": "presentation",
    ".potx": "presentation",
    ".potm": "presentation",
}


class ParserRegistry:
    def __init__(self, parsers: list[ParserPort]) -> None:
        self.parsers = parsers

    @classmethod
    def from_settings(cls, settings: Settings) -> ParserRegistry:
        registrations = _raw_parser_registrations(settings)
        if not bool(settings.get("parsers.isolation.enabled", False)):
            return cls([parser for _, parser in registrations])
        parser_config: JsonObject = TypeAdapter(JsonObject).validate_python(
            settings.get("parsers", {}) or {}
        )
        limits = _isolation_limits(settings)
        return cls(
            [
                IsolatedParserAdapter(
                    parser_key=key,
                    delegate=parser,
                    project_root=settings.project_root,
                    parser_config=parser_config,
                    limits=limits,
                )
                for key, parser in registrations
            ]
        )

    def find(self, path: Path) -> ParserPort:
        for parser in self.parsers:
            if parser.supports(path):
                return parser
        raise ParserError(f"no parser registered for {path.suffix.lower() or '<no extension>'}")

    def capabilities(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for parser in self.parsers:
            result[parser.name] = parser.version
        return result

    def representation_role(self, extension: str) -> str:
        return _REPRESENTATION_ROLE_BY_EXTENSION.get(extension.lower(), "primary")


def raw_parser_by_key(settings: Settings, parser_key: str) -> ParserPort:
    for key, parser in _raw_parser_registrations(settings):
        if key == parser_key:
            return parser
    raise ConfigurationError(f"unknown isolated parser key: {parser_key}")


def _raw_parser_registrations(settings: Settings) -> list[tuple[str, ParserPort]]:
    ocr = _kordoc_ocr(settings)
    pdf = PdfParser(
        ocr=ocr,
        tables_enabled=bool(settings.get("parsers.pdf.tables_enabled", True)),
    )
    hwp_configs: list[CommandParserConfig] = []
    native_parser: HwpNativeParser | None = None
    for name in settings.get("parsers.hwp.order", ["kordoc", "unhwp"]):
        if name == "paired_pdf":
            continue
        config = settings.get(f"parsers.hwp.{name}", {}) or {}
        if name == "hwp-hwpx-parser":
            if bool(config.get("enabled", False)):
                native_parser = HwpNativeParser(
                    max_chars_per_unit=int(config.get("max_chars_per_unit", 4000))
                )
            continue
        hwp_configs.append(
            CommandParserConfig(
                name=name,
                argv=[str(item) for item in config.get("argv", [])],
                enabled=bool(config.get("enabled", False)) and bool(config.get("argv")),
                timeout_seconds=int(settings.get("parsers.parser_timeout_seconds", 120)),
            )
        )
    return [
        ("csv", CsvTableParser()),
        ("plain", PlainTextParser()),
        ("xlsx", XlsxShallowParser()),
        ("docx", DocxParser()),
        ("pptx", PptxParser(ocr=ocr, ocr_limits=_pptx_ocr_limits(settings))),
        ("pdf", pdf),
        (
            "hwp",
            HwpParserChain(
                native_parser,
                HwpParserBroker(hwp_configs, paired_pdf_parser=pdf),
            ),
        ),
    ]


def _isolation_limits(settings: Settings) -> ParserIsolationLimits:
    return ParserIsolationLimits(
        wall_seconds=float(settings.get("parsers.isolation.wall_seconds", 180)),
        cpu_seconds=int(settings.get("parsers.isolation.cpu_seconds", 120)),
        memory_bytes=int(settings.get("parsers.isolation.memory_mib", 6144))
        * 1024
        * 1024,
        result_bytes=int(settings.get("parsers.isolation.result_mib", 256))
        * 1024
        * 1024,
        diagnostic_bytes=int(settings.get("parsers.isolation.diagnostic_kib", 16))
        * 1024,
        cpu_threads=int(settings.get("parsers.isolation.cpu_threads", 4)),
        nice=int(settings.get("parsers.isolation.nice", 5)),
    )


def _kordoc_ocr(settings: Settings) -> KordocOcrAdapter | None:
    config = settings.get("parsers.ocr.kordoc", {}) or {}
    if not isinstance(config, dict) or not bool(config.get("enabled", False)):
        return None
    argv = tuple(str(item) for item in config.get("argv", []))
    if not argv:
        raise ConfigurationError(
            "enabled Kordoc OCR requires parsers.ocr.kordoc.argv"
        )
    if Path(argv[0]).name in {"npm", "npx"}:
        raise ConfigurationError(
            "Kordoc OCR requires an installed Kordoc binary, not npm or npx"
        )
    expected_version = str(config.get("expected_version", "4.7.3"))
    if expected_version != KordocOcrAdapter.version:
        raise ConfigurationError(
            "Kordoc OCR adapter supports only pinned version 4.7.3"
        )
    version_argv = tuple(str(item) for item in config.get("version_argv", []))
    if not version_argv:
        raise ConfigurationError(
            "enabled Kordoc OCR requires parsers.ocr.kordoc.version_argv"
        )
    return KordocOcrAdapter(
        KordocOcrConfig(
            argv=argv,
            version_argv=version_argv,
            expected_version=expected_version,
            timeout_seconds=int(settings.get("parsers.ocr.timeout_seconds", 120)),
        )
    )


def _pptx_ocr_limits(settings: Settings) -> PptxOcrLimits:
    return PptxOcrLimits(
        max_images=int(settings.get("parsers.ocr.pptx.max_images", 128)),
        max_image_bytes=int(
            settings.get("parsers.ocr.pptx.max_image_bytes", 20 * 1024 * 1024)
        ),
        max_total_bytes=int(
            settings.get("parsers.ocr.pptx.max_total_bytes", 100 * 1024 * 1024)
        ),
        min_width_px=int(settings.get("parsers.ocr.pptx.min_width_px", 96)),
        min_height_px=int(settings.get("parsers.ocr.pptx.min_height_px", 48)),
    )
