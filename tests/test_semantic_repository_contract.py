from __future__ import annotations

from pathlib import Path

from kip.domain.models import (
    EmbeddingRecord,
    EmbeddingSpace,
    RequestContext,
    SearchRequest,
)
from kip.ids import stable_id


def test_embedding_spaces_coexist_and_vector_search_is_acl_safe(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "공개.txt").write_text("참여율 변경을 승인한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    repository = test_container.repository.retrieval
    units = repository.list_embeddable_units(context)
    assert len(units) == 1

    first = EmbeddingSpace(
        id=stable_id("espace", "default", "first"),
        name="first",
        provider="fixture",
        model="fixture-a",
        revision="a1",
        dimensions=3,
        normalized=True,
        status="shadow",
    )
    second = first.model_copy(
        update={
            "id": stable_id("espace", "default", "second"),
            "name": "second",
            "model": "fixture-b",
            "revision": "b1",
        }
    )
    repository.save_embedding_space(context, first)
    repository.save_embedding_space(context, second)
    repository.upsert_embeddings(
        context,
        first.id,
        [
            EmbeddingRecord(
                unit_id=units[0].unit_id,
                embedding=[1.0, 0.0, 0.0],
                source_hash=units[0].source_hash,
            )
        ],
    )
    repository.upsert_embeddings(
        context,
        second.id,
        [
            EmbeddingRecord(
                unit_id=units[0].unit_id,
                embedding=[0.0, 1.0, 0.0],
                source_hash=units[0].source_hash,
            )
        ],
    )
    repository.activate_embedding_space(context, first.id)

    hits = repository.vector_search(
        context,
        SearchRequest(query="승인", limit=10),
        [1.0, 0.0, 0.0],
        space_id=first.id,
        limit=10,
    )
    denied = RequestContext(
        workspace="default",
        principal_id="principal_denied",
        acl_scopes=[],
    )
    denied_hits = repository.vector_search(
        denied,
        SearchRequest(query="승인", limit=10),
        [1.0, 0.0, 0.0],
        space_id=first.id,
        limit=10,
    )

    assert [hit.document_id for hit in hits] == [units[0].document_id]
    assert denied_hits == []
    active = repository.active_embedding_space(context)
    assert active is not None
    assert active.id == first.id
    assert active.status == "active"
    assert repository.semantic_status(context)["spaces"] == 2
    assert repository.semantic_status(context)["vectors"] == 2


def test_stale_vectors_are_excluded_without_deleting_other_spaces(
    test_container,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    (source_root / "근거.txt").write_text("정산 증빙을 확인한다.", encoding="utf-8")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    repository = test_container.repository.retrieval
    unit = repository.list_embeddable_units(context)[0]
    first = EmbeddingSpace(
        id=stable_id("espace", "default", "first"),
        name="first",
        provider="fixture",
        model="fixture-a",
        revision="a1",
        dimensions=3,
        normalized=True,
        status="shadow",
    )
    second = first.model_copy(
        update={"id": stable_id("espace", "default", "second"), "name": "second"}
    )
    repository.save_embedding_space(context, first)
    repository.save_embedding_space(context, second)
    repository.upsert_embeddings(
        context,
        first.id,
        [EmbeddingRecord(unit_id=unit.unit_id, embedding=[1.0, 0.0, 0.0], source_hash="stale")],
    )
    repository.upsert_embeddings(
        context,
        second.id,
        [
            EmbeddingRecord(
                unit_id=unit.unit_id,
                embedding=[0.0, 1.0, 0.0],
                source_hash=unit.source_hash,
            )
        ],
    )

    hits = repository.vector_search(
        context,
        SearchRequest(query="증빙"),
        [1.0, 0.0, 0.0],
        space_id=first.id,
        limit=10,
    )

    assert hits == []
    assert repository.semantic_status(context)["vectors"] == 2
    assert test_container.repository.operations.status(context).content_units == 1
