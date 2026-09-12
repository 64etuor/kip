#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kip.adapters.repository.memory import MemoryRepository  # noqa: E402
from kip.api import create_app  # noqa: E402
from kip.container import build_container  # noqa: E402
from kip.domain.egress import EgressDecision  # noqa: E402
from kip.domain.generation import (  # noqa: E402
    GenerationRelation,
    GenerationRequest,
    GenerationResult,
    StructuredGenerationRequest,
    StructuredGenerationResult,
)
from kip.domain.interactions import (  # noqa: E402
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationRequest,
    ClarificationResolution,
    FeedbackSubmission,
    InteractionEvent,
    InteractionFeedback,
    OntologyDiscoveryCandidate,
    OntologyDiscoveryProposal,
    OntologyDiscoveryReview,
    UserPreference,
    UserPreferenceWrite,
)
from kip.domain.knowledge import (  # noqa: E402
    EntityCandidate,
    KnowledgeEntity,
    RelationMiningRequest,
    RelationMiningResult,
    RelationProposal,
)
from kip.domain.models import (  # noqa: E402
    AnswerRequest,
    AnswerResponse,
    ApprovedAssertion,
    Artifact,
    ArtifactView,
    AssertionCandidate,
    AssertionCandidateListing,
    AssertionExplanation,
    Capabilities,
    ConnectorEvent,
    ContentUnit,
    ContextBundle,
    ContextRequest,
    DocumentPacket,
    Envelope,
    EvidenceLocator,
    EvidenceRead,
    GraphEdge,
    GraphNeighborsRequest,
    GraphPath,
    GraphPathRequest,
    IngestResult,
    JobRecord,
    OntologyAnswerContext,
    OntologyMiningSubmission,
    OntologyMiningSummary,
    ReextractionSummary,
    SearchHit,
    SearchRequest,
    SourceObject,
    SourceRevision,
    StatusReport,
    SyncSummary,
    VocabularyItem,
    XlsxRangeRead,
)
from kip.domain.package_archive import PackageArchiveManifest  # noqa: E402
from kip.domain.telemetry import QueryTrace  # noqa: E402
from kip.evaluation.answers import (  # noqa: E402
    AnswerMetrics,
    AnswerReview,
)
from kip.evaluation.drafts import GoldenDraft, GoldenDraftReview  # noqa: E402
from kip.evaluation.models import GoldenDataset  # noqa: E402
from kip.evaluation.ontology import (  # noqa: E402
    OntologyMetrics,
    OntologyReview,
)
from kip.evaluation.reviews import EvaluationReviewBundle  # noqa: E402
from kip.ontology_migration import (  # noqa: E402
    OntologyMigration,
    OntologyMigrationMaterialization,
)
from kip.settings import Settings  # noqa: E402
from kip.setup.models import (  # noqa: E402
    SetupAnswers,
    SetupInspection,
    SetupPlan,
    SetupReceipt,
)

MODELS = {
    "envelope": Envelope,
    "source-object": SourceObject,
    "source-revision": SourceRevision,
    "artifact": Artifact,
    "artifact-view": ArtifactView,
    "answer-request": AnswerRequest,
    "answer-response": AnswerResponse,
    "content-unit": ContentUnit,
    "evidence-locator": EvidenceLocator,
    "document-packet": DocumentPacket,
    "connector-event": ConnectorEvent,
    "search-request": SearchRequest,
    "search-hit": SearchHit,
    "context-request": ContextRequest,
    "context-bundle": ContextBundle,
    "graph-neighbors-request": GraphNeighborsRequest,
    "graph-path-request": GraphPathRequest,
    "graph-edge": GraphEdge,
    "graph-path": GraphPath,
    "assertion-candidate": AssertionCandidate,
    "assertion-candidate-listing": AssertionCandidateListing,
    "approved-assertion": ApprovedAssertion,
    "assertion-explanation": AssertionExplanation,
    "xlsx-range-read": XlsxRangeRead,
    "evidence-read": EvidenceRead,
    "ingest-result": IngestResult,
    "sync-summary": SyncSummary,
    "reextraction-summary": ReextractionSummary,
    "job-record": JobRecord,
    "capabilities": Capabilities,
    "status-report": StatusReport,
    "vocabulary-item": VocabularyItem,
    "egress-decision": EgressDecision,
    "generation-request": GenerationRequest,
    "generation-relation": GenerationRelation,
    "generation-result": GenerationResult,
    "structured-generation-request": StructuredGenerationRequest,
    "structured-generation-result": StructuredGenerationResult,
    "knowledge-entity": KnowledgeEntity,
    "entity-candidate": EntityCandidate,
    "relation-mining-request": RelationMiningRequest,
    "relation-mining-result": RelationMiningResult,
    "ontology-mining-submission": OntologyMiningSubmission,
    "ontology-mining-summary": OntologyMiningSummary,
    "ontology-answer-context": OntologyAnswerContext,
    "ontology-migration": OntologyMigration,
    "ontology-migration-materialization": OntologyMigrationMaterialization,
    "relation-proposal": RelationProposal,
    "setup-answers": SetupAnswers,
    "setup-inspection": SetupInspection,
    "setup-plan": SetupPlan,
    "setup-receipt": SetupReceipt,
    "query-trace": QueryTrace,
    "clarification-request": ClarificationRequest,
    "clarification-question": ClarificationQuestion,
    "clarification-answer": ClarificationAnswer,
    "clarification-resolution": ClarificationResolution,
    "user-preference-write": UserPreferenceWrite,
    "user-preference": UserPreference,
    "feedback-submission": FeedbackSubmission,
    "interaction-feedback": InteractionFeedback,
    "interaction-event": InteractionEvent,
    "ontology-discovery-proposal": OntologyDiscoveryProposal,
    "ontology-discovery-candidate": OntologyDiscoveryCandidate,
    "ontology-discovery-review": OntologyDiscoveryReview,
    "answer-review": AnswerReview,
    "answer-metrics": AnswerMetrics,
    "ontology-review": OntologyReview,
    "ontology-metrics": OntologyMetrics,
    "golden-dataset": GoldenDataset,
    "evaluation-review-bundle": EvaluationReviewBundle,
    "golden-draft": GoldenDraft,
    "golden-draft-review": GoldenDraftReview,
    "package-manifest": PackageArchiveManifest,
}


# The API reads these three credentials directly from request headers
# (`src/kip/api.py`), so a client generated from this document must be told
# about them or it sends anonymous requests.
SECURITY_SCHEMES = {
    "ApiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-KIP-API-Key",
        "description": "Shared API key, used when identity.mode is api_key.",
    },
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Identity-provider JWT, used when identity.mode is proxy_jwt. "
            "Admin routes additionally require the admin group claim."
        ),
    },
    "AdminKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-KIP-Admin-Key",
        "description": (
            "Second factor for /v1/admin and other admin-only routes when "
            "identity.mode is api_key."
        ),
    },
}

CALLER_SECURITY = [{"ApiKeyAuth": []}, {"BearerAuth": []}]
ADMIN_SECURITY = [
    {"ApiKeyAuth": [], "AdminKeyAuth": []},
    {"BearerAuth": []},
]


def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Describe the payload a caller actually sees.

    A field marked `exclude=True` is never serialized, so publishing it from
    the validation-mode schema (which still lists it, and can mark it
    required) contradicts every payload on the wire.
    """
    excludes_fields = any(
        getattr(field, "exclude", False) for field in model.model_fields.values()
    )
    mode: Literal["serialization", "validation"] = (
        "serialization" if excludes_fields else "validation"
    )
    return model.model_json_schema(mode=mode)


def _dependency_names(dependant: Any) -> set[str]:
    names: set[str] = set()
    pending = [dependant]
    while pending:
        current = pending.pop()
        call = getattr(current, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        pending.extend(current.dependencies)
    return names


def _route_security(app: FastAPI) -> dict[tuple[str, str], list[dict[str, list[str]]]]:
    """Map (path, method) to the credentials that route actually requires."""
    security: dict[tuple[str, str], list[dict[str, list[str]]]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        names = _dependency_names(route.dependant)
        if "admin_context" in names:
            requirement = ADMIN_SECURITY
        elif "authenticated_context" in names:
            requirement = CALLER_SECURITY
        else:
            requirement = []
        for method in route.methods:
            security[(route.path, method.lower())] = requirement
    return security


def _apply_contract_corrections(app: FastAPI, openapi: dict[str, Any]) -> dict[str, Any]:
    """Make the published document match what the API returns and requires."""
    openapi["components"]["securitySchemes"] = SECURITY_SCHEMES
    security = _route_security(app)
    envelope_ref = {"$ref": "#/components/schemas/Envelope"}
    for path, operations in openapi["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            requirement = security.get((path, method))
            if requirement is not None:
                operation["security"] = requirement
            # FastAPI documents 422 as its own HTTPValidationError, but
            # `request_validation_handler` answers with the kip.envelope.v1
            # error envelope like every other failure.
            invalid = operation.get("responses", {}).get("422")
            if invalid is not None:
                invalid["description"] = "Validation error (kip.envelope.v1)"
                invalid["content"] = {"application/json": {"schema": envelope_ref}}
    schemas = openapi["components"]["schemas"]
    rendered = json.dumps(openapi["paths"])
    for orphan in ("HTTPValidationError", "ValidationError"):
        if orphan in schemas and f"#/components/schemas/{orphan}" not in rendered:
            del schemas[orphan]
    return openapi


def render_files() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for name, model in MODELS.items():
        path = ROOT / "contracts" / f"{name}.schema.json"
        text = json.dumps(_model_schema(model), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        files[path] = text

    settings = Settings.for_test()
    container = build_container(settings, repository=MemoryRepository())
    app = create_app(container)
    openapi = _apply_contract_corrections(app, app.openapi())
    files[ROOT / "contracts/openapi.json"] = json.dumps(openapi, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    files[ROOT / "contracts/openapi.yaml"] = yaml.safe_dump(openapi, allow_unicode=True, sort_keys=False)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_files()
    mismatches: list[str] = []
    for path, text in expected.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                mismatches.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(path.relative_to(ROOT))
    if mismatches:
        print("Generated contracts are stale:", file=sys.stderr)
        for item in mismatches:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
