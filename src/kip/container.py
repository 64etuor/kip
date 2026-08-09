from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.embeddings.http import HttpEmbeddingAdapter
from kip.adapters.embeddings.noop import DisabledEmbeddingAdapter
from kip.adapters.parsers.registry import ParserRegistry
from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.adapters.rerankers.http import HttpRerankerAdapter
from kip.adapters.rerankers.huggingface import (
    HuggingFaceJinaRerankerAdapter,
    RerankerBackend,
    parse_reranker_backend,
)
from kip.adapters.storage import (
    LocalContentAddressedStore,
    LocalSourceFileInspector,
    LocalWorkbookReader,
)
from kip.application.analyzer import KoreanNgramAnalyzer
from kip.application.evidence import EvidenceUseCases
from kip.application.ingestion import IngestionUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.operations import OperationsUseCases
from kip.application.runtime import Application
from kip.application.search import RetrievalUseCases
from kip.ontology import OntologyCatalog
from kip.ports.embedding import EmbeddingPort
from kip.ports.repository import RepositoryPort
from kip.ports.reranker import RerankerPort
from kip.settings import Settings


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    repository: RepositoryPort
    application: Application
    embedding: EmbeddingPort
    reranker: RerankerPort | None


def build_container(
    settings: Settings | None = None,
    repository: RepositoryPort | None = None,
    embedding: EmbeddingPort | None = None,
    reranker: RerankerPort | None = None,
    *,
    load_models: bool = True,
) -> Container:
    selected = settings or Settings.load()
    if repository is not None:
        selected_repository = repository
    elif selected.is_memory:
        selected_repository = MemoryRepository()
    else:
        selected_repository = PostgresRepository(
            selected.database_url,
            statement_timeout_ms=int(
                selected.get("database.statement_timeout_ms", 15000)
            ),
        )
    parsers = ParserRegistry.from_settings(selected)
    sources = ConfiguredSourceCatalog(selected)
    analyzer = KoreanNgramAnalyzer(
        min_n=int(selected.get("search.korean_ngram_min", 2)),
        max_n=int(selected.get("search.korean_ngram_max", 4)),
    )
    allow_remote_egress = bool(selected.get("security.allow_remote_model_egress", False))
    embedding_config = selected.get("models.embedding", {}) or {}
    selected_embedding = embedding
    if (
        load_models
        and selected_embedding is None
        and embedding_config.get("enabled", False)
    ):
        selected_embedding = HttpEmbeddingAdapter(
            base_url=str(embedding_config.get("base_url", "http://127.0.0.1:7997")),
            model=str(embedding_config["model"]),
            revision=str(embedding_config["revision"]),
            dimensions=int(embedding_config.get("dimensions", 1024)),
            query_instruction=str(embedding_config.get("query_instruction", "")),
            allow_remote_egress=allow_remote_egress,
            timeout_seconds=float(embedding_config.get("timeout_seconds", 30)),
        )
    selected_embedding = selected_embedding or DisabledEmbeddingAdapter()

    reranker_config = selected.get("models.reranker", {}) or {}
    selected_reranker = reranker
    if load_models and selected_reranker is None and reranker_config.get("enabled", False):
        backend = parse_reranker_backend(str(reranker_config.get("backend", "http")))
        match backend:
            case RerankerBackend.HTTP:
                selected_reranker = HttpRerankerAdapter(
                    base_url=str(reranker_config.get("base_url", "http://127.0.0.1:7997")),
                    model=str(reranker_config["model"]),
                    revision=str(reranker_config["revision"]),
                    allow_remote_egress=allow_remote_egress,
                    timeout_seconds=float(reranker_config.get("timeout_seconds", 30)),
                )
            case RerankerBackend.HUGGINGFACE:
                selected_reranker = HuggingFaceJinaRerankerAdapter(
                    model=str(reranker_config["model"]),
                    revision=str(reranker_config["revision"]),
                    max_length=int(reranker_config.get("max_length", 1024)),
                    device=str(reranker_config["device"])
                    if reranker_config.get("device")
                    else None,
                )
            case unreachable:
                assert_never(unreachable)
    selected.cas_path.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceUseCases(
        selected_repository.evidence,
        LocalSourceFileInspector(),
        LocalWorkbookReader(),
    )
    retrieval = RetrievalUseCases(
        selected,
        selected_repository.retrieval,
        selected_repository.operations,
        evidence,
        analyzer,
        selected_embedding,
        selected_reranker,
    )
    ontology_root = selected.project_root / "ontology"
    ontology = (
        OntologyCatalog.load(ontology_root) if ontology_root.is_dir() else None
    )
    application = Application(
        ingestion=IngestionUseCases(
            selected_repository.ingestion,
            selected_repository.jobs,
            sources,
            parsers,
            analyzer,
            LocalContentAddressedStore(selected.cas_path),
        ),
        retrieval=retrieval,
        evidence=evidence,
        knowledge=KnowledgeUseCases(
            selected_repository.knowledge,
            evidence,
            ontology,
        ),
        operations=OperationsUseCases(
            selected,
            selected_repository.operations,
            selected_repository.jobs,
            selected_repository.retrieval,
            sources,
            parsers,
            selected_embedding,
        ),
    )
    return Container(
        settings=selected,
        repository=selected_repository,
        application=application,
        embedding=selected_embedding,
        reranker=selected_reranker,
    )
