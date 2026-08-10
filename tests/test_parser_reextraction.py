from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.models import DocumentPacket, SearchRequest
from kip.errors import ConflictError
from kip.ids import new_id, stable_id
from kip.settings import Settings


class _FakeReader:
    text = "기존계약문구"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.tables: list[object] = []

    def __enter__(self) -> _FakeReader:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_text(self) -> str:
        return self.text

    def get_images(self) -> list[object]:
        return []


def _native_hwp_container(
    tmp_path: Path,
    *,
    minimum_quality_score: float = 0.70,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "fixture.hwp").write_bytes(
        bytes.fromhex("D0CF11E0A1B11AE1") + b"fixture"
    )
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "search": {"semantic_enabled": False},
            "parsers": {
                "minimum_quality_score": minimum_quality_score,
                "hwp": {
                    "order": ["hwp-hwpx-parser"],
                    "hwp-hwpx-parser": {"enabled": True},
                },
            },
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".hwp"],
                        "acl_scope": "workspace:default",
                    }
                ]
            },
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
    )
    return build_container(settings, repository=MemoryRepository())


def _candidate_packet(
    repository: MemoryRepository,
    *,
    body: str,
) -> DocumentPacket:
    packet = next(iter(repository.state.packets_by_revision.values()))
    extraction_id = new_id("ext")
    extraction = packet.extraction.model_copy(
        update={
            "id": extraction_id,
            "parser_name": "replacement-parser",
            "parser_version": "2.0",
            "output_hash": "b" * 64,
        },
        deep=True,
    )
    unit = packet.units[0].model_copy(
        update={
            "id": stable_id("unit", extraction_id, "0"),
            "extraction_id": extraction_id,
            "body": body,
            "body_normalized": body,
            "lexical_text": body,
        },
        deep=True,
    )
    return packet.model_copy(
        update={"extraction": extraction, "units": [unit]},
        deep=True,
    )


def test_replacing_extraction_swaps_search_units_and_retains_history(
    test_container,
) -> None:
    # Given an indexed source revision with one active extraction.
    source = test_container.settings.project_root / "source" / "evidence.txt"
    source.write_text("기존 검색 근거", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    repository = test_container.repository
    assert isinstance(repository, MemoryRepository)
    original = next(iter(repository.state.packets_by_revision.values()))
    candidate = _candidate_packet(repository, body="교체된 검색 근거")

    # When the approved candidate replaces the active extraction.
    result = repository.ingestion.replace_extraction(context, candidate)

    # Then only candidate units are searchable and both extraction records remain auditable.
    replacement_hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="교체된"),
    )
    original_hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="기존"),
    )
    assert result.status == "replaced"
    assert replacement_hits[0].unit_id == candidate.units[0].id
    assert original_hits == []
    assert original.extraction.id in repository.state.extraction_packets
    assert candidate.extraction.id in repository.state.extraction_packets


def test_replacing_extraction_rejects_a_non_current_source_revision(
    test_container,
) -> None:
    # Given an indexed source and a candidate bound to a different source hash.
    source = test_container.settings.project_root / "source" / "evidence.txt"
    source.write_text("기존 검색 근거", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    repository = test_container.repository
    assert isinstance(repository, MemoryRepository)
    original = next(iter(repository.state.packets_by_revision.values()))
    candidate = _candidate_packet(repository, body="허용되지 않은 교체")
    stale = candidate.model_copy(
        update={
            "revision": candidate.revision.model_copy(
                update={"sha256": "c" * 64},
                deep=True,
            )
        },
        deep=True,
    )

    # When replacement is attempted against the stale candidate.
    with pytest.raises(ConflictError, match="current source revision"):
        repository.ingestion.replace_extraction(context, stale)

    # Then the original extraction remains the only searchable projection.
    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="기존"),
    )
    assert hits[0].unit_id == original.units[0].id
    assert candidate.extraction.id not in repository.state.extraction_packets


def test_hwp_reextraction_is_shadow_only_until_explicit_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an indexed HWP whose parser output changes without changing the source file.
    monkeypatch.setitem(
        sys.modules,
        "hwp_hwpx_parser",
        SimpleNamespace(Reader=_FakeReader),
    )
    _FakeReader.text = "기존계약문구"
    container = _native_hwp_container(tmp_path)
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    repository = container.repository
    assert isinstance(repository, MemoryRepository)
    original_extraction_count = len(repository.state.extraction_packets)
    _FakeReader.text = "혁신교체문구"

    # When the operator first runs shadow parsing and then explicitly activates it.
    shadow = container.application.ingestion.reextract_filesystem(
        context,
        "fixture",
        activate=False,
    )
    shadow_hits = container.application.retrieval.search(
        context,
        SearchRequest(query="기존계약"),
    )
    activated = container.application.ingestion.reextract_filesystem(
        context,
        "fixture",
        activate=True,
    )

    # Then shadow mode is non-mutating and activation atomically exposes only new units.
    assert shadow.parsed == 1
    assert shadow.activated == 0
    assert shadow.parser_counts == {"hwp-hwpx-parser": 1}
    assert shadow_hits
    assert activated.activated == 1
    assert len(repository.state.extraction_packets) == original_extraction_count + 1
    assert container.application.retrieval.search(
        context,
        SearchRequest(query="혁신교체"),
    )
    assert container.application.retrieval.search(
        context,
        SearchRequest(query="기존계약"),
    ) == []


def test_hwp_reextraction_rejects_candidates_below_the_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an indexed HWP and a configured quality threshold above parser output.
    monkeypatch.setitem(
        sys.modules,
        "hwp_hwpx_parser",
        SimpleNamespace(Reader=_FakeReader),
    )
    _FakeReader.text = "기존계약문구"
    container = _native_hwp_container(tmp_path, minimum_quality_score=0.99)
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    _FakeReader.text = "품질미달교체"

    # When activation is requested for the lower-quality candidate.
    summary = container.application.ingestion.reextract_filesystem(
        context,
        "fixture",
        activate=True,
    )

    # Then the candidate is reported as rejected and the active evidence is unchanged.
    assert summary.rejected == 1
    assert summary.activated == 0
    assert container.application.retrieval.search(
        context,
        SearchRequest(query="기존계약"),
    )
    assert container.application.retrieval.search(
        context,
        SearchRequest(query="품질미달"),
    ) == []
