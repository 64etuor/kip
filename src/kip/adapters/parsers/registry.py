from __future__ import annotations

from pathlib import Path
from typing import Any

from kip.adapters.parsers.docx import DocxParser
from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker
from kip.adapters.parsers.pdf import PdfParser
from kip.adapters.parsers.plain import PlainTextParser
from kip.adapters.parsers.xlsx import XlsxShallowParser
from kip.errors import ParserError
from kip.settings import Settings


class ParserRegistry:
    def __init__(self, parsers: list[Any]) -> None:
        self.parsers = parsers

    @classmethod
    def from_settings(cls, settings: Settings) -> "ParserRegistry":
        pdf = PdfParser()
        hwp_configs: list[CommandParserConfig] = []
        for name in settings.get("parsers.hwp.order", ["kordoc", "unhwp"]):
            if name == "paired_pdf":
                continue
            config = settings.get(f"parsers.hwp.{name}", {}) or {}
            hwp_configs.append(
                CommandParserConfig(
                    name=name,
                    argv=[str(item) for item in config.get("argv", [])],
                    enabled=bool(config.get("enabled", False)) and bool(config.get("argv")),
                    timeout_seconds=int(settings.get("parsers.parser_timeout_seconds", 120)),
                )
            )
        return cls(
            [
                PlainTextParser(),
                XlsxShallowParser(),
                DocxParser(),
                pdf,
                HwpParserBroker(hwp_configs, paired_pdf_parser=pdf),
            ]
        )

    def find(self, path: Path) -> Any:
        for parser in self.parsers:
            if parser.supports(path):
                return parser
        raise ParserError(f"no parser registered for {path.suffix.lower() or '<no extension>'}")

    def capabilities(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for parser in self.parsers:
            result[parser.name] = getattr(parser, "version", "unknown")
        return result
