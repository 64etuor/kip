import os
import uuid
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unicodedata import normalize

import pytest

from kip.adapters.repository.postgres import PostgresRepository
from kip.container import build_container
from kip.domain.file_references import FilenameSearchRequest
from kip.domain.models import AnswerRequest, SearchRequest


@pytest.mark.parametrize('query', [
    '대상.txt 최종 승인일은 언제야?',
    '"대상.txt" 최종 승인일은 언제야?',
    '`대상.txt` 최종 승인일은 언제야?',
])
@pytest.mark.parametrize('limit', [1, 5])
def test_named_file_never_answers_from_other_document(test_container, query, limit):
    source = test_container.settings.project_root / 'source'
    (source / '대상.txt').write_text('검토 초안이며 승인 여부는 결정되지 않았다.')
    (source / '다른문서.txt').write_text('최종 승인일은 2026년 9월 3일이다. 최종 승인일에 사업을 승인했다.')
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, 'fixture')
    hits = test_container.application.retrieval.search(context, SearchRequest(query=query, limit=limit))
    response = test_container.application.answering.answer(context, AnswerRequest(query=query, limit=limit))
    assert hits[0].metadata["file_name"] == "다른문서.txt"
    assert response.refused, 'A named, unapproved document must not borrow another file approval'


def test_stale_named_file_never_falls_back_to_other_file(test_container):
    source = test_container.settings.project_root / 'source'
    target = source / '대상.txt'
    target.write_text('대상 최종 승인일은 2026년 9월 2일이다. 승인했다.')
    (source / '다른문서.txt').write_text('최종 승인일은 2026년 9월 3일이다. 최종 승인일에 사업을 승인했다.')
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, 'fixture')
    target.write_text('내용이 수정되어 최신 승인은 아직 확정되지 않았다. 오래된 날짜를 사용하지 않는다.')
    response = test_container.application.answering.answer(context, AnswerRequest(query='대상.txt 최종 승인일은 언제야?'))
    assert response.refused
    assert response.refusal_reason == "no_fresh_evidence"


@pytest.mark.parametrize('query', [
    '제출기한은 언제야?',
    '제출기한은 언제인가?',
    '담당자는 누구야?',
    '담당자는 누구인가?',
    '제출서류는 무엇인가?',
    normalize("NFD", '제출기한은 언제인가?'),
])
def test_question_endings_are_not_required_evidence_terms(test_container, query):
    source = test_container.settings.project_root / 'source' / '안내.txt'
    source.write_text('제출기한은 2026년 9월 30일이다. 담당자는 김하늘이다. 제출서류는 신청서이다.')
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, 'fixture')
    response = test_container.application.answering.answer(context, AnswerRequest(query=query))
    assert not response.refused


@pytest.fixture
def postgres_container(test_container):
    import psycopg

    url = os.environ.get('KIP_TEST_POSTGRES_URL') or os.environ.get('KIP_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL not configured')
    workspace = 'review_' + uuid.uuid4().hex[:12]
    raw = deepcopy(test_container.settings.raw)
    raw['sources']['filesystem'][0]['acl_scope'] = f'workspace:{workspace}'
    settings = replace(test_container.settings, raw=raw, workspace=workspace, database_url=url)
    repository = PostgresRepository(url)
    repository.operations.migrate(Path.cwd() / 'migrations')
    try:
        yield build_container(settings, repository=repository)
    finally:
        repository.database.close()
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute('DELETE FROM kip.workspaces WHERE slug=%s', (workspace,))


@pytest.mark.parametrize('query,limit', [
    ('대상.txt 최종 승인일은 언제야?', 1),
    ('"대상.txt" 최종 승인일은 언제야?', 5),
])
def test_postgres_named_file_scope(postgres_container, query, limit):
    test_named_file_never_answers_from_other_document(postgres_container, query, limit)


def test_postgres_stale_named_file_scope(postgres_container):
    test_stale_named_file_never_falls_back_to_other_file(postgres_container)


@pytest.mark.parametrize("backend", ["test_container", "postgres_container"])
def test_exclusion_and_unavailable_file_scope_are_not_limited(backend, request):
    container = request.getfixturevalue(backend)
    source = container.settings.project_root / "source"
    (source / "제외.txt").write_text("정산 기한은 2026년 10월 10일이다. 정산 기한 정산 기한")
    (source / "대상.txt").write_text("정산 기한은 2026년 11월 11일이다.")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    response = container.application.answering.answer(context, AnswerRequest(query='"제외.txt" 말고 정산 기한은?', limit=1))
    assert not response.refused
    assert "11월 11일" in response.answer
    unknown = container.application.answering.answer(context, AnswerRequest(query='"없는파일.txt" 정산 기한은?'))
    outsider = container.application.answering.answer(
        context.model_copy(update={"acl_scopes": []}), AnswerRequest(query='"대상.txt" 정산 기한은?')
    )
    assert unknown.refused and outsider.refused
    assert unknown.citations == outsider.citations == []
    assert unknown.refusal_reason == outsider.refusal_reason == "no_admissible_evidence"
    container.settings.raw["sources"]["filesystem"] = []
    reloaded = build_container(container.settings, repository=container.repository)
    removed = reloaded.application.answering.answer(context, AnswerRequest(query='"대상.txt" 정산 기한은?'))
    assert removed.refused and removed.citations == []


@pytest.mark.parametrize("backend", ["test_container", "postgres_container"])
def test_file_scope_precedes_vector_nearest_limit(backend, request):
    from kip.domain.models import EmbeddingRecord, EmbeddingSpace

    container = request.getfixturevalue(backend)
    source = container.settings.project_root / "source"
    for name in ("대상", "다른문서"):
        (source / f"{name}.txt").write_text(f"{name} 문서 내용")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    store = container.repository.retrieval
    units = store.list_embeddable_units(context)
    space = EmbeddingSpace(id="espace_"+uuid.uuid4().hex, name="scope-test", provider="fixture", model="fixture", revision="v1", dimensions=1024, normalized=True, status="shadow")
    store.save_embedding_space(context, space)
    nearest = [1.0] + [0.0] * 1023
    farther = [0.0, 1.0] + [0.0] * 1022
    store.upsert_embeddings(context, space.id, [
        EmbeddingRecord(unit_id=unit.unit_id, embedding=farther if "대상" in unit.body_normalized else nearest, source_hash=unit.source_hash)
        for unit in units
    ])
    hits = store.vector_search(context, FilenameSearchRequest(query="내용", limit=1, included_filenames=["대상.txt"]), nearest, space_id=space.id, limit=1)
    assert len(hits) == 1 and hits[0].metadata["file_name"] == "대상.txt"


@pytest.mark.parametrize("backend", ["test_container", "postgres_container"])
def test_unicode_casefold_ambiguity_is_the_same_in_both_backends(backend, request):
    container = request.getfixturevalue(backend)
    source = container.settings.project_root / "source"
    (source / "a").mkdir()
    (source / "b").mkdir()
    (source / "a" / "straße.txt").write_text("A 사업 검토 중")
    (source / "b" / "STRASSE.txt").write_text("B 사업 검토 중")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    response = container.application.answering.answer(context, AnswerRequest(query='"STRASSE.txt"', limit=1))
    assert response.refused and response.refusal_reason == "clarification_required"


def test_ontology_cannot_restore_evidence_outside_scoped_candidates(test_container, monkeypatch):
    from kip.application.ontology_context import OntologyEvidenceContext

    source = test_container.settings.project_root / "source"
    (source / "대상.txt").write_text("아직 승인되지 않은 검토 초안이다.")
    (source / "다른문서.txt").write_text("최종 승인일은 2026년 9월 3일이다. 승인했다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    hit = test_container.application.retrieval.search(context, SearchRequest(query="다른문서.txt"))[0]
    other = test_container.application.evidence.read_unit(context, hit.unit_id)
    monkeypatch.setattr(test_container.application.answering._ontology_context, "build", lambda *args, **kwargs: OntologyEvidenceContext(context=None, evidence=(other,)))

    response = test_container.application.answering.answer(context, AnswerRequest(query='"대상.txt" 최종 승인일은 언제인가?', limit=1))
    assert response.refused
    assert response.citations == []


@pytest.mark.parametrize("stale", [False, True])
def test_multi_file_request_cannot_silently_drop_one_named_file(test_container, stale):
    source = test_container.settings.project_root / "source"
    (source / "A.txt").write_text("담당자는 기획팀이다.")
    (source / "B.txt").write_text("담당자는 회계팀이다.")
    context = test_container.application.operations.request_context()
    test_container.application.ingestion.sync_filesystem(context, "fixture")
    if stale:
        (source / "A.txt").write_text("문서가 변경됐다. 담당자는 아직 정해지지 않았다.")
    response = test_container.application.answering.answer(
        context, AnswerRequest(query='"A.txt"와 "B.txt"의 담당자는?', limit=5 if stale else 1),
    )
    assert response.refused and response.citations == []
    assert response.refusal_reason == ("no_fresh_evidence" if stale else "no_admissible_evidence")
