from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.egress import DataClassification
from kip.domain.identity import AclSnapshot
from kip.domain.models import DocumentPacket, SearchRequest
from kip.errors import ConflictError, ValidationError
from kip.ids import new_id, stable_id
from kip.ports.ingestion import DiscoveredFile
from kip.settings import Settings


def _clean_hwp_text(phrase: str) -> str:
    """Pad a short fixture phrase to a realistic, high-quality document size.

    HwpNativeParser now scores quality from content (see
    ``kip.adapters.parsers.text_quality.hwp_text_quality``) instead of a
    flat constant, and that formula weighs extracted length. A bare 6-8
    character phrase alone would score well below the default
    ``minimum_quality_score`` gate even though it is otherwise clean
    Hangul text, so repeat it to a length comparable to a real document
    while keeping the identifying phrase searchable.
    """
    return phrase * 400


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
                        "include_extensions": [".hwp", ".pdf"],
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
    _FakeReader.text = _clean_hwp_text("혁신교체문구")

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


def test_hwp_reextraction_does_not_hash_other_configured_formats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an indexed HWP and a non-HWP file in the same configured source.
    monkeypatch.setitem(
        sys.modules,
        "hwp_hwpx_parser",
        SimpleNamespace(Reader=_FakeReader),
    )
    _FakeReader.text = "기존계약문구"
    container = _native_hwp_container(tmp_path)
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    source_root = tmp_path / "source"
    (source_root / "unrelated.pdf").write_bytes(b"%PDF-1.7 unrelated")
    hashed_suffixes: list[str] = []
    original_property = DiscoveredFile.sha256

    def tracked_hash(self: DiscoveredFile) -> str:
        if self._sha256 is None:
            hashed_suffixes.append(self.path.suffix.lower())
        return original_property.fget(self)

    monkeypatch.setattr(DiscoveredFile, "sha256", property(tracked_hash))

    # When the operator prepares an HWP-only re-extraction.
    summary = container.application.ingestion.reextract_filesystem(
        context,
        "fixture",
        activate=False,
    )

    # Then the connector never reads or hashes unrelated configured formats.
    assert summary.parsed == 1
    assert hashed_suffixes == [".hwp"]


def test_hwp_reextraction_does_not_reauthorize_a_foreign_source_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    current = next(iter(repository.state.packets_by_revision.values()))
    canonical_snapshot = AclSnapshot.configuration(
        snapshot_id=new_id("aclsnap"),
        version="legacy-canonical",
        provider="legacy-migration",
        scopes=["workspace:default"],
    )
    repository.ingestion.upsert_acl_snapshot(
        context,
        current.source_object.id,
        canonical_snapshot,
        DataClassification.RESTRICTED,
    )
    _FakeReader.text = _clean_hwp_text("교체된계약문구")

    summary = container.application.ingestion.reextract_filesystem(
        context,
        "fixture",
        activate=True,
    )

    active = next(iter(repository.state.packets_by_revision.values()))
    assert summary.activated == 0
    assert summary.failed == 1
    assert active.source_object.acl_snapshot == canonical_snapshot
    assert all(
        unit.acl_snapshot_id == canonical_snapshot.id for unit in active.units
    )


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


def test_pdf_reextraction_is_opt_in_by_extension_and_rejects_unparsable_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given an indexed HWP and an indexed native-text PDF in one source.
    import pymupdf

    monkeypatch.setitem(
        sys.modules,
        "hwp_hwpx_parser",
        SimpleNamespace(Reader=_FakeReader),
    )
    _FakeReader.text = "기존계약문구"
    container = _native_hwp_container(tmp_path)
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Quarterly budget evidence for the PDF upgrade")
    document.save(tmp_path / "source" / "report.pdf")
    document.close()
    context = container.application.operations.request_context()
    synced = container.application.ingestion.sync_filesystem(context, "fixture")
    assert synced.inserted == 2
    ingestion = container.application.ingestion

    # When the default run and an explicit PDF run are prepared.
    default = ingestion.reextract_filesystem(context, "fixture")
    pdf_shadow = ingestion.reextract_filesystem(context, "fixture", extensions=frozenset({"PDF"}))
    pdf_active = ingestion.reextract_filesystem(
        context, "fixture", activate=True, extensions=frozenset({".pdf"})
    )

    # Then the default keeps its HWP/HWPX scope and PDFs are re-extracted only on request.
    assert default.extensions == [".hwp", ".hwpx"]
    assert default.parser_counts == {"hwp-hwpx-parser": 1}
    assert pdf_shadow.extensions == [".pdf"]
    assert (pdf_shadow.parsed, pdf_shadow.activated) == (1, 0)
    assert pdf_shadow.parser_counts == {"pdf-inspector": 1}
    assert (pdf_active.parsed, pdf_active.activated, pdf_active.failed) == (1, 1, 0)
    assert container.application.retrieval.search(context, SearchRequest(query="Quarterly budget"))

    # And the signature-matched defaults can be named explicitly next to other formats.
    both = ingestion.reextract_filesystem(context, "fixture", extensions=frozenset({".HWP", "pdf"}))
    assert both.extensions == [".hwp", ".pdf"]
    assert both.parser_counts == {"hwp-hwpx-parser": 1, "pdf-inspector": 1}
    assert not any("were found" in warning for warning in both.warnings)

    # And a requested format the source does not contain warns instead of passing silently.
    empty = ingestion.reextract_filesystem(context, "fixture", extensions=frozenset({".docx"}))
    assert empty.scanned == 0
    assert any("no .docx files were found" in warning for warning in empty.warnings)

    # And a suffix without a registered parser is an operator error, not a silent no-op.
    with pytest.raises(ValidationError, match=r"no parser is registered for \.xyz"):
        ingestion.reextract_filesystem(context, "fixture", extensions=frozenset({"xyz"}))
    with pytest.raises(ValidationError, match="invalid file extension"):
        ingestion.reextract_filesystem(context, "fixture", extensions=frozenset({"../pdf"}))
