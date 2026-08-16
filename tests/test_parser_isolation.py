from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from kip.adapters.parsers.isolation import (
    IsolatedParserAdapter,
    ParserWorkerRequest,
    ParserWorkerSuccess,
)
from kip.adapters.parsers.plain import PlainTextParser
from kip.adapters.parsers.process_supervisor import BoundedProcessResult, ParserIsolationLimits
from kip.adapters.parsers.registry import ParserRegistry
from kip.errors import ParserError
from kip.settings import Settings


def test_worker_request_round_trips_versioned_json() -> None:
    # Given a typed parser worker request
    request = ParserWorkerRequest(
        parser_key="pdf",
        source_path="/nas/document.pdf",
        project_root="/project",
        parser_config={"pdf": {"tables_enabled": True}},
        artifact_id="art_test",
        document_id="doc_test",
        acl_scopes=("workspace:test",),
    )

    # When it crosses the child-process JSON boundary
    restored = ParserWorkerRequest.model_validate_json(request.model_dump_json())

    # Then the version and immutable request values are preserved
    assert restored == request
    assert restored.schema_version == "kip.parser-request.v1"


def test_isolated_parser_preserves_plain_parser_contract(tmp_path: Path) -> None:
    # Given the same parser behind the raw and isolated adapter boundaries
    source = tmp_path / "document.txt"
    source.write_text("NAS 검색 격리 검증", encoding="utf-8")
    raw = PlainTextParser()
    isolated = IsolatedParserAdapter(
        parser_key="plain",
        delegate=raw,
        project_root=Path.cwd(),
        parser_config={},
        limits=ParserIsolationLimits.for_test(),
    )

    # When both adapters parse the same document
    raw_extraction, raw_units = raw.parse(
        source,
        artifact_id="art_test",
        document_id="doc_test",
        acl_scopes=["workspace:test"],
    )
    isolated_extraction, isolated_units = isolated.parse(
        source,
        artifact_id="art_test",
        document_id="doc_test",
        acl_scopes=["workspace:test"],
    )

    # Then the external adapter preserves observable parser semantics
    assert isolated.name == raw.name
    assert isolated.version == raw.version
    assert isolated.supports(source)
    assert isolated_extraction.model_dump(exclude={"id"}) == raw_extraction.model_dump(
        exclude={"id"}
    )
    assert [unit.body for unit in isolated_units] == [unit.body for unit in raw_units]
    assert [unit.locator for unit in isolated_units] == [unit.locator for unit in raw_units]
    assert [unit.acl_scopes for unit in isolated_units] == [
        unit.acl_scopes for unit in raw_units
    ]


def test_isolated_parser_rejects_success_payload_from_failed_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a child that writes a valid success response but exits abnormally
    source = tmp_path / "document.txt"
    source.write_text("NAS 검색 격리 검증", encoding="utf-8")
    raw = PlainTextParser()
    extraction, units = raw.parse(
        source,
        artifact_id="art_test",
        document_id="doc_test",
        acl_scopes=["workspace:test"],
    )
    response = ParserWorkerSuccess(
        extraction=extraction,
        units=tuple(units),
    ).model_dump_json()
    monkeypatch.setattr(
        "kip.adapters.parsers.isolation.run_bounded_process",
        lambda *args, **kwargs: BoundedProcessResult(
            returncode=70,
            response=response.encode("utf-8"),
            diagnostic="internal failure",
        ),
    )
    isolated = IsolatedParserAdapter(
        parser_key="plain",
        delegate=raw,
        project_root=Path.cwd(),
        parser_config={},
        limits=ParserIsolationLimits.for_test(),
    )

    # When the parent reconciles the response with the process status
    # Then an abnormal process cannot silently activate a success payload
    with pytest.raises(ParserError, match="exited unsuccessfully"):
        isolated.parse(
            source,
            artifact_id="art_test",
            document_id="doc_test",
            acl_scopes=["workspace:test"],
        )


def test_registry_wraps_every_parser_when_isolation_is_enabled() -> None:
    # Given a production-style parser isolation configuration
    settings = Settings.for_test()
    settings.raw["parsers"] = {
        "isolation": {"enabled": True},
        "hwp": {"order": []},
        "ocr": {"kordoc": {"enabled": False}},
    }

    # When the composition root builds the parser registry
    registry = ParserRegistry.from_settings(settings)

    # Then every selected parser is behind the external process adapter
    assert len(registry.parsers) == 7
    assert all(isinstance(parser, IsolatedParserAdapter) for parser in registry.parsers)


def test_isolated_parser_failure_does_not_expose_source_path(tmp_path: Path) -> None:
    # Given an invalid child parser key and a sensitive source path
    source = tmp_path / "sensitive-name.txt"
    source.write_text("content", encoding="utf-8")
    isolated = IsolatedParserAdapter(
        parser_key="unknown",
        delegate=PlainTextParser(),
        project_root=Path.cwd(),
        parser_config={},
        limits=ParserIsolationLimits.for_test(),
    )

    # When the child rejects its configuration
    with pytest.raises(ParserError) as captured:
        isolated.parse(
            source,
            artifact_id="art_test",
            document_id="doc_test",
            acl_scopes=["workspace:test"],
        )

    # Then the parent exposes only the stable sanitized error
    assert str(captured.value) == "parser process configuration is invalid"
    assert str(source) not in str(captured.value)


@pytest.mark.parametrize(
    "config_path",
    (
        Path("config/kip.example.toml"),
        Path("config/kip.container.toml"),
    ),
)
def test_reference_configs_ship_measured_m4_parser_isolation(
    config_path: Path,
) -> None:
    # Given a checked-in NAS runtime profile
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)

    # When the parser isolation table is inspected
    isolation = config["parsers"]["isolation"]

    # Then it carries the measured serial M4 Pro 24 GB resource budget
    assert isolation == {
        "enabled": True,
        "wall_seconds": 180,
        "cpu_seconds": 120,
        "memory_mib": 6144,
        "result_mib": 256,
        "diagnostic_kib": 16,
        "cpu_threads": 4,
        "nice": 5,
    }
