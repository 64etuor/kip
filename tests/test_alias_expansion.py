from __future__ import annotations

from kip.domain.knowledge import KnowledgeEntity
from kip.domain.models import SearchRequest


def _seed(test_container, *, entity_scopes: list[str] | None = None) -> None:
    source = test_container.settings.project_root / "source"
    (source / "관리지침.txt").write_text(
        "협력업체 신용등급은 B 이상이어야 한다.", encoding="utf-8"
    )
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    test_container.repository.knowledge.save_entity(
        context,
        KnowledgeEntity(
            id="ent_supplier",
            entity_type="organization",
            canonical_name="협력업체",
            aliases=["공급업체", "거래처"],
            acl_scopes=entity_scopes or [],
        ),
    )


def _score(test_container, *, expansion: bool) -> float:
    test_container.settings.raw["search"]["alias_expansion_enabled"] = expansion
    context = test_container.application.operations.request_context()
    hits = test_container.application.retrieval.search(
        context,
        SearchRequest(query="공급업체 등록", limit=10),
    )
    for hit in hits:
        if hit.title.startswith("관리지침"):
            return hit.score
    return 0.0


def test_approved_alias_expansion_boosts_the_canonical_document(test_container):
    _seed(test_container)

    boosted = _score(test_container, expansion=True)
    baseline = _score(test_container, expansion=False)

    assert boosted > baseline


def test_expansion_respects_entity_acl_scopes(test_container):
    _seed(test_container, entity_scopes=["project:secret"])

    with_flag = _score(test_container, expansion=True)
    baseline = _score(test_container, expansion=False)

    # The entity is outside the caller's scopes, so expansion adds nothing.
    assert with_flag == baseline
