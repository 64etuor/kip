from __future__ import annotations

from pathlib import Path

from kip.adapters.parsers.docx import DocxParser
from kip.adapters.parsers.hwp_broker import CommandParserConfig, HwpParserBroker
from kip.adapters.parsers.hwp_native import HwpNativeParser, HwpParserChain
from kip.adapters.parsers.pdf import PdfParser
from kip.adapters.parsers.plain import PlainTextParser
from kip.adapters.parsers.xlsx import XlsxShallowParser
from kip.errors import ParserError
from kip.ports.parser import ParserPort
from kip.settings import Settings


class ParserRegistry:
    def __init__(self, parsers: list[ParserPort]) -> None:
        self.parsers = parsers

    @classmethod
    def from_settings(cls, settings: Settings) -> ParserRegistry:
        pdf = PdfParser()
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
        return cls(
            [
                PlainTextParser(),
                XlsxShallowParser(),
                DocxParser(),
                pdf,
                HwpParserChain(
                    native_parser,
                    HwpParserBroker(hwp_configs, paired_pdf_parser=pdf),
                ),
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
