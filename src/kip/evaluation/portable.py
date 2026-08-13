from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kip.adapters.repository.memory import MemoryRepository
from kip.container import build_container
from kip.domain.identity import AclSnapshot
from kip.domain.models import (
    Artifact,
    ContentUnit,
    DocumentPacket,
    EvidenceLocator,
    ExtractionRun,
    LogicalDocument,
    SearchHit,
    SearchRequest,
    SourceObject,
    SourceRevision,
)
from kip.evaluation.models import GoldenCase
from kip.evaluation.portable_contract import (
    PortableDocument,
    PortableSuite,
    expand_portable_dataset,
    load_portable_suite,
)
from kip.evaluation.runner import run_evaluation
from kip.settings import Settings


def _packet(suite: PortableSuite, document: PortableDocument) -> DocumentPacket:
    slug = document.id.casefold().replace("-", "_")
    digest = hashlib.sha256(document.body.encode()).hexdigest()
    snapshot = AclSnapshot.configuration(
        snapshot_id=f"aclsnap_{slug}",
        version=suite.version,
        provider="portable-fixture",
        scopes=[suite.acl_scope],
        captured_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    source_id = f"srcobj_{slug}"
    revision_id = f"rev_{slug}"
    artifact_id = f"art_{slug}"
    extraction_id = f"ext_{slug}"
    return DocumentPacket(
        workspace_id=suite.workspace,
        source_object=SourceObject(
            id=source_id,
            system_id="srcsys_portable",
            system_name="portable-regression",
            system_kind="fixture",
            external_id=document.id,
            object_type="file",
            canonical_uri=f"fixture://portable/{document.id}",
            acl_scopes=[suite.acl_scope],
            acl_snapshot=snapshot,
        ),
        revision=SourceRevision(
            id=revision_id,
            object_id=source_id,
            revision_key=digest,
            sha256=digest,
            size_bytes=len(document.body.encode()),
        ),
        logical_document=LogicalDocument(
            id=document.document_id,
            stable_key=f"portable:{document.id}",
            title=document.title,
            document_type="procedure",
        ),
        artifact=Artifact(
            id=artifact_id,
            revision_id=revision_id,
            file_name=f"{document.code}.txt",
            extension=".txt",
            media_type="text/plain",
            byte_size=len(document.body.encode()),
            sha256=digest,
        ),
        extraction=ExtractionRun(
            id=extraction_id,
            artifact_id=artifact_id,
            parser_name="portable-fixture",
            parser_version=suite.version,
            status="succeeded",
            quality_score=1.0,
            output_hash=digest,
        ),
        units=[
            ContentUnit(
                id=f"unit_{slug}",
                extraction_id=extraction_id,
                document_id=document.document_id,
                artifact_id=artifact_id,
                ordinal=0,
                unit_type="text_document",
                title=document.title,
                body=document.body,
                body_normalized=document.body,
                lexical_text=document.body,
                locator=EvidenceLocator(
                    type="text_line_range",
                    data={"start_line": 1, "end_line": 1},
                ),
                acl_scopes=[suite.acl_scope],
                acl_snapshot_id=snapshot.id,
            )
        ],
    )


def run_portable_gate(path: Path, *, project_root: Path) -> dict[str, Any]:
    suite_bytes = path.read_bytes()
    suite = load_portable_suite(path)
    dataset = expand_portable_dataset(suite, suite_bytes)
    repository = MemoryRepository()
    settings = Settings(
        project_root=project_root,
        config_path=project_root / "config/kip.example.toml",
        raw={
            "app": {"workspace": suite.workspace},
            "search": {"semantic_enabled": False},
            "graph": {"backend": "memory"},
        },
        environment="test",
        workspace=suite.workspace,
        database_url="memory://",
        cas_path=project_root / "var/portable-gate-cas",
    )
    container = build_container(settings, repository=repository)
    ingest_context = container.application.operations.request_context(
        workspace=suite.workspace,
        principal_id="principal_portable",
        acl_scopes=[suite.acl_scope],
    )
    for document in suite.documents:
        repository.ingestion.ingest_packet(ingest_context, _packet(suite, document))

    def search(case: GoldenCase, variant: str) -> list[SearchHit]:
        context = container.application.operations.request_context(
            workspace=suite.workspace,
            principal_id=case.principal,
            acl_scopes=case.acl_scopes,
        )
        return container.application.retrieval.search(
            context,
            SearchRequest(query=case.question, limit=case.recall_at),
            mode=variant,
        )

    return run_evaluation(
        dataset,
        variants=["lexical"],
        search=search,
        workspace=suite.workspace,
        dataset_bytes=suite_bytes,
        configuration=settings.raw,
        code_root=project_root,
        warmup_passes=0,
    )


def portable_gate_failures(report: dict[str, Any]) -> list[str]:
    result = report["variants"]["lexical"]
    metrics = result["metrics"]
    failures: list[str] = []
    if int(metrics["case_count"]) < 100:
        failures.append("case count is below 100")
    if float(metrics["recall_at_k"]) < 1.0:
        failures.append("recall@k is below 1.0")
    if float(metrics["mrr"]) < 1.0:
        failures.append("MRR is below 1.0")
    if int(metrics["failed_case_count"]) != 0:
        failures.append("one or more cases raised an error")
    if int(metrics["unauthorized_result_count"]) != 0:
        failures.append("an ACL-forbidden document was returned")
    if float(result["latency_ms"]["p95"]) > 100.0:
        failures.append("portable P95 exceeded 100ms")
    return failures
