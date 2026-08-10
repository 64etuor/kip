from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from kip.adapters.repository.memory import MemoryRepository
from kip.api import create_app
from kip.container import build_container
from kip.domain.generation import (
    GeneratedClaim,
    GenerationRequest,
    GenerationResult,
    GenerationUsage,
    ModelRevision,
)
from kip.domain.models import AnswerRequest
from kip.errors import DependencyUnavailableError
from kip.settings import Settings


class RecordingGenerator:
    name = "recording"

    def __init__(
        self,
        *,
        provider: str = "local",
        claims: tuple[GeneratedClaim, ...] | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.provider = provider
        self.model = "fixture-model"
        self.revision = "sha256:fixture"
        self.claims = claims
        self.failure = failure
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        claims = self.claims or (
            GeneratedClaim(
                text="정산 증빙 제출기한은 2026년 8월 15일이다.",
                evidence_ids=(request.evidence[0].id,),
                certainty="supported",
            ),
        )
        return GenerationResult(
            claims=claims,
            model=ModelRevision(
                provider=self.provider,
                model=self.model,
                revision=self.revision,
            ),
            usage=GenerationUsage(input_tokens=20, output_tokens=8, total_tokens=28),
            provider_request_id="req_fixture",
        )


def _container(
    tmp_path: Path,
    generator: RecordingGenerator,
    *,
    classification: str = "restricted",
    fallback_on_error: bool = False,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    remote = generator.provider in {"openai", "anthropic"}
    settings = Settings(
        project_root=tmp_path,
        config_path=tmp_path / "kip.toml",
        raw={
            "security": {"allow_remote_model_egress": remote},
            "search": {
                "semantic_enabled": False,
                "korean_ngram_min": 2,
                "korean_ngram_max": 4,
            },
            "graph": {"backend": "memory"},
            "models": {
                "generation": {
                    "enabled": True,
                    "provider": generator.provider,
                    "base_url": (
                        "http://127.0.0.1:7998"
                        if not remote
                        else f"https://api.{generator.provider}.test"
                    ),
                    "model": generator.model,
                    "revision": generator.revision,
                    "allowed_classifications": ["public"],
                    "retention_policy": "zero_retention",
                    "secret_ref": f"env:KIP_{generator.provider.upper()}_API_KEY",
                    "fallback_on_error": fallback_on_error,
                }
            },
            "sources": {
                "filesystem": [
                    {
                        "name": "fixture",
                        "root": str(source_root),
                        "enabled": True,
                        "read_only": True,
                        "settle_seconds": 0,
                        "include_extensions": [".txt", ".xlsx"],
                        "exclude_globs": [],
                        "acl_scope": "workspace:default",
                        "classification": classification,
                    }
                ]
            },
            "parsers": {"hwp": {"order": ["paired_pdf"]}},
        },
        environment="test",
        workspace="default",
        database_url="memory://",
        cas_path=tmp_path / "cas",
        api_key="test-key",
        admin_key="test-admin",
    )
    return build_container(
        settings,
        repository=MemoryRepository(),
        generator=generator,
    )


def _ingest(container, filename: str, body: str) -> Path:
    path = container.settings.project_root / "source" / filename
    path.write_text(body, encoding="utf-8")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")
    return path


def test_generated_answer_uses_reopened_fresh_evidence_and_exact_citations(
    tmp_path: Path,
) -> None:
    generator = RecordingGenerator()
    container = _container(tmp_path, generator)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")
    context = container.application.operations.request_context()

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )

    assert response.refused is False
    assert response.retrieval_mode == "generated"
    assert response.answer == "정산 증빙 제출기한은 2026년 8월 15일이다."
    assert response.claims[0].evidence_ids == (
        response.citations[0].unit_id,
    )
    assert generator.requests[0].evidence[0].body.endswith("8월 15일이다.")
    assert response.generation is not None
    assert response.generation.provider_request_id == "req_fixture"


def test_generation_receives_evidence_the_lexical_gate_would_drop(
    tmp_path: Path,
) -> None:
    generator = RecordingGenerator()
    container = _container(tmp_path, generator)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")
    context = container.application.operations.request_context()

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="증빙 마감 언제까지야?", limit=5),
    )

    assert response.refused is False
    assert generator.requests
    assert generator.requests[0].evidence[0].body.endswith("8월 15일이다.")


def test_unknown_generated_citation_returns_typed_refusal(tmp_path: Path) -> None:
    generator = RecordingGenerator(
        claims=(
            GeneratedClaim(
                text="근거 없는 주장",
                evidence_ids=("unit_unknown",),
                certainty="supported",
            ),
        )
    )
    container = _container(tmp_path, generator)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")

    response = container.application.answering.answer(
        container.application.operations.request_context(),
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )

    assert response.refused is True
    assert response.refusal_reason == "generation_invalid"
    assert response.citations == []


def test_remote_egress_denial_is_explicit_and_generator_is_not_called(
    tmp_path: Path,
) -> None:
    generator = RecordingGenerator(provider="openai")
    container = _container(tmp_path, generator, classification="restricted")
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")

    response = container.application.answering.answer(
        container.application.operations.request_context(),
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )

    assert response.refused is True
    assert response.refusal_reason == "model_egress_denied"
    assert response.egress_decision is not None
    assert response.egress_decision.denial_reason == "classification_not_allowed"
    assert generator.requests == []


def test_generator_failure_refuses_by_default(tmp_path: Path) -> None:
    generator = RecordingGenerator(
        failure=DependencyUnavailableError("provider unavailable")
    )
    container = _container(tmp_path, generator)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")

    response = container.application.answering.answer(
        container.application.operations.request_context(),
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )

    assert response.refused is True
    assert response.refusal_reason == "generation_unavailable"
    assert response.answer == "구조화 생성기를 사용할 수 없어 답변을 확정하지 않았습니다."


def test_generator_failure_uses_extractive_only_when_explicitly_configured(
    tmp_path: Path,
) -> None:
    generator = RecordingGenerator(
        failure=DependencyUnavailableError("provider unavailable")
    )
    container = _container(tmp_path, generator, fallback_on_error=True)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")

    response = container.application.answering.answer(
        container.application.operations.request_context(),
        AnswerRequest(query="정산 증빙 제출기한", limit=5),
    )

    assert response.refused is False
    assert response.retrieval_mode == "extractive"
    assert response.warnings == ["generation_unavailable_extractive_fallback"]


def test_stale_evidence_never_reaches_generator(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    container = _container(tmp_path, generator)
    path = _ingest(container, "승인.txt", "A과제 참여율 변경은 승인되었다.")
    path.write_text("A과제 참여율 변경은 검토 중이다.", encoding="utf-8")

    response = container.application.answering.answer(
        container.application.operations.request_context(),
        AnswerRequest(query="A과제 참여율 변경 승인", limit=5),
    )

    assert response.refusal_reason == "no_fresh_evidence"
    assert generator.requests == []


def test_xlsx_numeric_intent_requires_exact_range_before_generation(
    tmp_path: Path,
) -> None:
    generator = RecordingGenerator()
    container = _container(tmp_path, generator)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "정산"
    sheet.append(["항목", "금액"])
    sheet.append(["인건비", 1_500_000])
    workbook.save(tmp_path / "source" / "정산.xlsx")
    context = container.application.operations.request_context()
    container.application.ingestion.sync_filesystem(context, "fixture")

    response = container.application.answering.answer(
        context,
        AnswerRequest(query="인건비 금액", limit=5),
    )

    assert response.refusal_reason == "exact_xlsx_read_required"
    assert generator.requests == []


def test_rest_answer_uses_same_generated_answer_service(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    container = _container(tmp_path, generator)
    _ingest(container, "제출기한.txt", "정산 증빙 제출기한은 2026년 8월 15일이다.")
    client = TestClient(create_app(container))

    response = client.post(
        "/v1/answer",
        headers={"X-KIP-API-Key": "test-key"},
        json={"query": "정산 증빙 제출기한", "limit": 5},
    )

    assert response.status_code == 200
    assert response.json()["data"]["retrieval_mode"] == "generated"
    assert response.json()["data"]["generation"]["provider_request_id"] == "req_fixture"
