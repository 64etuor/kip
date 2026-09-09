from __future__ import annotations

import copy
import unicodedata

import pytest
from test_filesystem_access_policy import scoped_container as scoped_container

from kip.container import build_container
from kip.domain.models import SearchRequest


def _seed(container, filename="quartz_registry_739.txt"):
    path = container.settings.project_root / "source" / filename
    path.write_text("ordinary unrelated content", encoding="utf-8")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    return context, path


def test_filename_only_query_survives_abstention_and_ranks_first(scoped_container):
    context, path = _seed(scoped_container)
    source = path.parent
    for index in range(5):
        (source / f"noise-{index}.txt").write_text("quartz registry 739 " * 20, encoding="utf-8")
    scoped_container.application.ingestion.sync_filesystem(context, "fixture")
    hits = scoped_container.application.retrieval.search(
        context, SearchRequest(query="quartz_registry_739", limit=1)
    )
    assert len(hits) == 1
    assert hits[0].metadata["file_name"] == path.name


def test_filename_only_vocabulary_does_not_abstain(scoped_container):
    context, path = _seed(scoped_container)
    hits = scoped_container.application.retrieval.search(
        context, SearchRequest(query="quartz_registry_739", limit=1)
    )
    assert len(hits) == 1
    assert hits[0].metadata["file_name"] == path.name


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_korean_filename_normalization_parity(scoped_container, form):
    context, _ = _seed(scoped_container, unicodedata.normalize("NFD", "특별지침문서.txt"))
    hits = scoped_container.application.retrieval.search(
        context, SearchRequest(query=unicodedata.normalize(form, "특별지침문서"))
    )
    assert len(hits) == 1


def test_filename_literal_wildcards_do_not_match_other_identifiers(scoped_container):
    context, _ = _seed(scoped_container, "quartzXregistryY739.txt")
    for query in ("quartz_registry%739", "quartz%registry_739"):
        assert scoped_container.application.retrieval.search(context, SearchRequest(query=query)) == []
    literal = scoped_container.settings.project_root / "source" / "quartz_registry%739.txt"
    literal.write_text("other unrelated content", encoding="utf-8")
    scoped_container.application.ingestion.sync_filesystem(context, "fixture")
    hits = scoped_container.application.retrieval.search(
        context, SearchRequest(query="quartz_registry%739", limit=1)
    )
    assert len(hits) == 1
    assert hits[0].metadata["file_name"] == literal.name


def test_filename_lookup_preserves_scope_and_filters(scoped_container):
    context, _ = _seed(scoped_container)
    query = "quartz_registry_739"
    repository = scoped_container.repository.retrieval
    assert repository.has_identifier_match(context, SearchRequest(query=query))
    for request in (
        SearchRequest(query=query, source_kinds=["slack"]),
        SearchRequest(query=query, document_types=["not-a-document-type"]),
        SearchRequest(query=query, project_ids=["other-project"]),
    ):
        assert not repository.has_identifier_match(context, request)
        assert scoped_container.application.retrieval.search(context, request) == []
    denied = context.model_copy(update={"acl_scopes": []})
    assert not repository.has_identifier_match(denied, SearchRequest(query=query))
    assert scoped_container.application.retrieval.search(denied, SearchRequest(query=query)) == []
    foreign = context.model_copy(update={"workspace": "not-this-workspace"})
    assert not repository.has_identifier_match(foreign, SearchRequest(query=query))
    assert scoped_container.application.retrieval.search(foreign, SearchRequest(query=query)) == []


def test_removed_root_and_nonsense_still_abstain(scoped_container):
    context, _ = _seed(scoped_container)
    assert scoped_container.application.retrieval.search(context, SearchRequest(query="imaginaryextraterrestrial")) == []
    settings = copy.deepcopy(scoped_container.settings)
    settings.raw["sources"]["filesystem"].clear()
    application = build_container(settings, repository=scoped_container.repository).application
    assert application.retrieval.search(context, SearchRequest(query="quartz_registry_739")) == []
