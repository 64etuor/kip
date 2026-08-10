from __future__ import annotations

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.domain.models import DocumentPacket, SearchRequest
from kip.errors import ConflictError
from kip.ids import new_id, stable_id


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
