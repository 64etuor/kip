from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.embeddings.http import HttpEmbeddingAdapter
from kip.adapters.embeddings.noop import DisabledEmbeddingAdapter
from kip.adapters.generators.anthropic import AnthropicGenerationAdapter
from kip.adapters.generators.openai_compatible import OpenAICompatibleGenerationAdapter
from kip.adapters.identity import (
    ApiKeyIdentityAdapter,
    JwtIdentityAdapter,
    JwtIdentityConfig,
)
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
from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.ingestion import IngestionUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.operations import OperationsUseCases
from kip.application.runtime import Application
from kip.application.search import RetrievalUseCases
from kip.domain.egress import (
    DataClassification,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
)
from kip.errors import ConfigurationError
from kip.ontology import OntologyCatalog
from kip.ports.embedding import EmbeddingPort
from kip.ports.generation import GenerationPort
from kip.ports.identity import IdentityResolverPort
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
    generator: GenerationPort | None
    identity: IdentityResolverPort


def build_container(
    settings: Settings | None = None,
    repository: RepositoryPort | None = None,
    embedding: EmbeddingPort | None = None,
    reranker: RerankerPort | None = None,
    generator: GenerationPort | None = None,
    *,
    load_models: bool = True,
) -> Container:
    selected = settings or Settings.load()
    selected_identity = _build_identity(selected)
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
    selected_generator = generator
    generation_config = selected.get("models.generation", {}) or {}
    if not isinstance(generation_config, dict):
        raise ConfigurationError("models.generation must be a table")
    if (
        load_models
        and selected_generator is None
        and generation_config.get("enabled", False)
    ):
        selected_generator = _build_generator(
            selected,
            generation_config,
            allow_remote_egress=allow_remote_egress,
        )
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
        egress=EgressPolicyUseCases(_build_egress_policy(selected)),
    )
    return Container(
        settings=selected,
        repository=selected_repository,
        application=application,
        embedding=selected_embedding,
        reranker=selected_reranker,
        generator=selected_generator,
        identity=selected_identity,
    )


def _build_generator(
    settings: Settings,
    raw: dict[str, object],
    *,
    allow_remote_egress: bool,
) -> GenerationPort:
    provider = str(raw.get("provider", "")).strip()
    model = str(raw.get("model", "")).strip()
    revision = str(raw.get("revision", "")).strip()
    if not model or not revision:
        raise ConfigurationError(
            "enabled generation requires pinned model and revision values"
        )
    timeout_seconds = float(str(raw.get("timeout_seconds", 60)))
    max_response_bytes = int(str(raw.get("max_response_bytes", 1024 * 1024)))
    if provider == "local":
        return OpenAICompatibleGenerationAdapter(
            base_url=str(raw.get("base_url", "http://127.0.0.1:7998")),
            api_key="",
            model=model,
            revision=revision,
            provider="local",
            allow_remote_egress=False,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
    if provider not in {"openai", "anthropic"}:
        raise ConfigurationError(f"unsupported generation provider: {provider or 'missing'}")
    secret_reference = str(raw.get("secret_ref", "")).strip()
    if not secret_reference:
        raise ConfigurationError("remote generation requires secret_ref")
    api_key = settings.resolve_secret_reference(secret_reference)
    base_url = str(
        raw.get(
            "base_url",
            "https://api.openai.com" if provider == "openai" else "https://api.anthropic.com",
        )
    )
    if provider == "openai":
        return OpenAICompatibleGenerationAdapter(
            base_url=base_url,
            api_key=api_key,
            model=model,
            revision=revision,
            allow_remote_egress=allow_remote_egress,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
    return AnthropicGenerationAdapter(
        base_url=base_url,
        api_key=api_key,
        model=model,
        revision=revision,
        allow_remote_egress=allow_remote_egress,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
    )


def _build_identity(settings: Settings) -> IdentityResolverPort:
    mode = settings.identity_mode
    if mode == "api_key":
        scopes = settings.identity_api_key_acl_scopes or (
            f"workspace:{settings.workspace}",
        )
        return ApiKeyIdentityAdapter(
            expected_api_key=settings.api_key,
            workspace=settings.workspace,
            principal_id=settings.identity_api_key_principal_id,
            acl_scopes=scopes,
            allow_anonymous=settings.environment in {"development", "test"}
            and not settings.api_key,
        )
    if mode == "proxy_jwt":
        algorithms = settings.get("identity.jwt.algorithms", ["RS256"])
        if not isinstance(algorithms, list):
            raise ValueError("identity.jwt.algorithms must be a list")
        return JwtIdentityAdapter(
            JwtIdentityConfig(
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                jwks_url=settings.jwt_jwks_url,
                algorithms=tuple(str(item) for item in algorithms),
                principal_claim=str(
                    settings.get("identity.jwt.principal_claim", "sub")
                ),
                workspace_claim=str(
                    settings.get("identity.jwt.workspace_claim", "workspace")
                ),
                group_claim=str(settings.get("identity.jwt.group_claim", "groups")),
                scope_claim=str(
                    settings.get("identity.jwt.scope_claim", "acl_scopes")
                ),
                group_scope_prefix=str(
                    settings.get("identity.jwt.group_scope_prefix", "group:")
                ),
                admin_groups=tuple(
                    str(item)
                    for item in settings.get("identity.jwt.admin_groups", []) or []
                ),
                snapshot_id_claim=str(
                    settings.get(
                        "identity.jwt.snapshot_id_claim",
                        "acl_snapshot_id",
                    )
                ),
                snapshot_version_claim=str(
                    settings.get(
                        "identity.jwt.snapshot_version_claim",
                        "acl_snapshot_version",
                    )
                ),
                snapshot_captured_at_claim=str(
                    settings.get(
                        "identity.jwt.snapshot_captured_at_claim",
                        "acl_snapshot_captured_at",
                    )
                ),
                snapshot_expires_at_claim=str(
                    settings.get(
                        "identity.jwt.snapshot_expires_at_claim",
                        "acl_snapshot_expires_at",
                    )
                ),
                jwks_cache_seconds=float(
                    settings.get("identity.jwt.jwks_cache_seconds", 300)
                ),
                jwks_timeout_seconds=float(
                    settings.get("identity.jwt.jwks_timeout_seconds", 5)
                ),
                clock_skew_seconds=float(
                    settings.get("identity.jwt.clock_skew_seconds", 30)
                ),
            )
        )
    raise ValueError(f"unsupported identity mode: {mode}")


def _build_egress_policy(settings: Settings) -> EgressPolicy:
    raw = settings.get("models.generation", {}) or {}
    if not isinstance(raw, dict):
        raise ConfigurationError("models.generation must be a table")
    enabled = bool(raw.get("enabled", False))
    provider_value = str(raw.get("provider", "")).strip()
    if provider_value == "disabled":
        provider_value = ""
    try:
        provider = EgressProvider(provider_value) if provider_value else None
        classifications = tuple(
            DataClassification(str(item))
            for item in raw.get("allowed_classifications", []) or []
        )
        retention_value = str(raw.get("retention_policy", "")).strip()
        retention = RetentionPolicy(retention_value) if retention_value else None
    except ValueError as exc:
        raise ConfigurationError("invalid generation egress policy") from exc
    return EgressPolicy(
        enabled=enabled,
        provider=provider,
        allow_remote=bool(
            settings.get("security.allow_remote_model_egress", False)
        ),
        allowed_classifications=classifications,
        retention_policy=retention,
        secret_reference=str(raw.get("secret_ref", "")).strip() or None,
        base_url=str(raw.get("base_url", "")).strip() or None,
    )
