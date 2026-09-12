from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_never

from kip.adapters.analyzers import KoreanNgramAnalyzer
from kip.adapters.connectors.registry import ConfiguredSourceCatalog
from kip.adapters.embeddings.http import HttpEmbeddingAdapter
from kip.adapters.embeddings.noop import DisabledEmbeddingAdapter
from kip.adapters.generators.anthropic import AnthropicGenerationAdapter
from kip.adapters.generators.openai_compatible import OpenAICompatibleGenerationAdapter
from kip.adapters.generators.provider import (
    GenerationProviderKind,
    parse_generation_provider_kind,
)
from kip.adapters.identity import (
    ApiKeyIdentityAdapter,
    JwtIdentityAdapter,
    JwtIdentityConfig,
)
from kip.adapters.model_circuit import GuardedEmbedding, GuardedReranker, ModelCircuit
from kip.adapters.parsers.registry import ParserRegistry
from kip.adapters.relation_miners import GeneratorRelationMiner
from kip.adapters.repository.memory import MemoryRepository
from kip.adapters.repository.postgres import PostgresRepository
from kip.adapters.rerankers import (
    Bm25RerankerAdapter,
    HttpRerankerAdapter,
    RapidFuzzRerankerAdapter,
    RerankerBackend,
    parse_reranker_backend,
)
from kip.adapters.storage import (
    LocalContentAddressedStore,
    LocalSourceFileInspector,
    LocalWorkbookReader,
)
from kip.application.answering import AnsweringUseCases
from kip.application.egress import EgressPolicyUseCases
from kip.application.evidence import EvidenceUseCases
from kip.application.ingestion import IngestionUseCases
from kip.application.interactions import InteractionUseCases
from kip.application.knowledge import KnowledgeUseCases
from kip.application.ontology_context import OntologyContextUseCases
from kip.application.ontology_migrations import OntologyMigrationUseCases
from kip.application.ontology_rag import OntologyRagUseCases
from kip.application.operations import OperationsUseCases
from kip.application.runtime import Application
from kip.application.search import RetrievalUseCases
from kip.application.semantic import EMBEDDING_DEFAULTS
from kip.application.telemetry import TelemetryUseCases
from kip.domain.egress import (
    DataClassification,
    EgressPolicy,
    EgressProvider,
    RetentionPolicy,
    normalize_model_service_hosts,
)
from kip.errors import ConfigurationError
from kip.ontology import OntologyCatalog
from kip.ontology_discovery_release import complete_pending_release_locked, has_pending_release
from kip.ports.embedding import EmbeddingPort
from kip.ports.generation import GenerationPort
from kip.ports.identity import IdentityResolverPort
from kip.ports.relation_miner import RelationMinerPort
from kip.ports.repository import RepositoryPort
from kip.ports.reranker import RerankerPort
from kip.ports.telemetry import QueryTraceExporter
from kip.settings import Settings


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    repository: RepositoryPort
    application: Application
    embedding: EmbeddingPort
    reranker: RerankerPort | None
    lexical_reranker: RerankerPort | None
    generator: GenerationPort | None
    relation_miner: RelationMinerPort | None
    identity: IdentityResolverPort
    trace_exporters: tuple[QueryTraceExporter, ...]
    ontology: OntologyCatalog | None


def build_container(
    settings: Settings | None = None,
    repository: RepositoryPort | None = None,
    embedding: EmbeddingPort | None = None,
    reranker: RerankerPort | None = None,
    generator: GenerationPort | None = None,
    relation_miner: RelationMinerPort | None = None,
    trace_exporters: tuple[QueryTraceExporter, ...] | None = None,
    *,
    load_models: bool = True,
    lexical_reranker: RerankerPort | None = None,
) -> Container:
    selected = settings or Settings.load()
    selected_identity = _build_identity(selected)
    sources = ConfiguredSourceCatalog(selected)
    source_policy = sources.filesystem_access_policy()
    if repository is not None:
        selected_repository = repository
    elif selected.is_memory:
        selected_repository = MemoryRepository(source_policy=source_policy)
    else:
        selected_repository = PostgresRepository(
            selected.database_url,
            source_policy=source_policy,
            statement_timeout_ms=selected.database_statement_timeout_ms,
            pool_max_size=selected.database_pool_max_size,
            hnsw_ef_search=int(selected.get("search.hnsw_ef_search", 200)),
            hnsw_max_scan_tuples=int(
                selected.get("search.hnsw_max_scan_tuples", 100_000)
            ),
            lexical_common_term_fraction=float(
                selected.get("search.lexical_common_term_fraction", 0.02)
            ),
            projection_statement_timeout_ms=int(
                selected.get("database.projection_statement_timeout_ms", 300_000)
            ),
        )
    parsers = ParserRegistry.from_settings(selected)
    # Injected repositories obey the same deployment boundary as built-ins.
    selected_repository.configure_source_access(source_policy)
    analyzer = KoreanNgramAnalyzer(
        min_n=int(selected.get("search.korean_ngram_min", 2)),
        max_n=int(selected.get("search.korean_ngram_max", 4)),
    )
    allow_remote_egress = bool(selected.get("security.allow_remote_model_egress", False))
    model_circuit_seconds = float(selected.get("models.circuit_cooldown_seconds", 30))
    model_service_hosts = _model_service_hosts(selected)
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
            query_instruction=str(
                embedding_config.get("query_instruction", EMBEDDING_DEFAULTS["query_instruction"])
            ),
            allow_remote_egress=allow_remote_egress,
            timeout_seconds=float(
                embedding_config.get("timeout_seconds", EMBEDDING_DEFAULTS["timeout_seconds"])
            ),
            query_timeout_seconds=float(embedding_config.get("query_timeout_seconds", 10)),
            model_service_hosts=model_service_hosts,
        )
        selected_embedding = GuardedEmbedding(
            selected_embedding,
            ModelCircuit(cooldown_seconds=model_circuit_seconds),
        )
    selected_embedding = selected_embedding or DisabledEmbeddingAdapter()

    reranker_config = selected.get("models.reranker", {}) or {}
    backend = (
        parse_reranker_backend(str(reranker_config.get("backend", "bm25")))
        if reranker is None and reranker_config.get("enabled", False)
        else None
    )
    selected_reranker = reranker
    if backend is not None:
        match backend:
            case RerankerBackend.RAPIDFUZZ | RerankerBackend.BM25:
                selected_reranker = _build_lexical_reranker(backend, reranker_config)
            case RerankerBackend.HTTP:
                if load_models:
                    selected_reranker = GuardedReranker(
                        HttpRerankerAdapter(
                            base_url=str(
                                reranker_config.get(
                                    "base_url",
                                    "http://127.0.0.1:7997",
                                )
                            ),
                            model=str(reranker_config["model"]),
                            revision=str(reranker_config["revision"]),
                            allow_remote_egress=allow_remote_egress,
                            timeout_seconds=float(
                                reranker_config.get("timeout_seconds", 120)
                            ),
                            max_document_chars=int(
                                reranker_config.get("max_document_chars", 2048)
                            ),
                            model_service_hosts=model_service_hosts,
                        ),
                        ModelCircuit(cooldown_seconds=model_circuit_seconds),
                    )
            case unreachable:
                assert_never(unreachable)
    # Lexical mode and the `semantic_degraded` fallback keep a local lexical
    # reranker even when `reranked` mode uses a model cross-encoder, so they
    # neither wait on nor fail with the model runtime.
    selected_lexical_reranker = lexical_reranker
    if selected_lexical_reranker is None:
        if reranker is not None:
            selected_lexical_reranker = reranker
        elif backend in _LEXICAL_RERANKER_BACKENDS:
            selected_lexical_reranker = selected_reranker
        elif backend is not None:
            lexical_config = selected.get("models.lexical_reranker", {}) or {}
            if not isinstance(lexical_config, dict):
                raise ConfigurationError("models.lexical_reranker must be a table")
            lexical_backend = parse_reranker_backend(
                str(lexical_config.get("backend", RerankerBackend.BM25.value))
            )
            if lexical_backend not in _LEXICAL_RERANKER_BACKENDS:
                raise ConfigurationError(
                    "models.lexical_reranker.backend must be bm25 or rapidfuzz"
                )
            selected_lexical_reranker = _build_lexical_reranker(
                lexical_backend, lexical_config
            )
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
            model_service_hosts=model_service_hosts,
        )
    selected.cas_path.mkdir(parents=True, exist_ok=True)
    source_files = LocalSourceFileInspector(source_policy=source_policy)
    telemetry_config = selected.get("telemetry", {}) or {}
    if not isinstance(telemetry_config, dict):
        raise ConfigurationError("telemetry must be a table")
    query_traces_enabled = telemetry_config.get("query_traces_enabled", True)
    if not isinstance(query_traces_enabled, bool):
        raise ConfigurationError("telemetry.query_traces_enabled must be boolean")
    # KIP ships no trace exporter: `QueryTraceStore` in PostgreSQL is the
    # canonical trace record. A deployment that wants traces forwarded injects
    # its own `QueryTraceExporter` here through `build_container`.
    selected_trace_exporters: tuple[QueryTraceExporter, ...] = trace_exporters or ()
    telemetry = TelemetryUseCases(
        selected_repository.telemetry,
        enabled=query_traces_enabled,
        retention_days=_bounded_integer(
            telemetry_config,
            "telemetry",
            "retention_days",
            default=30,
            minimum=1,
            maximum=3650,
        ),
        exporters=selected_trace_exporters,
    )
    evidence = EvidenceUseCases(
        selected_repository.evidence,
        source_files,
        LocalWorkbookReader(),
        source_policy=source_policy,
    )
    retrieval = RetrievalUseCases(
        selected,
        selected_repository.retrieval,
        evidence,
        analyzer,
        selected_embedding,
        selected_reranker,
        telemetry,
        selected_repository.knowledge,
        lexical_reranker=selected_lexical_reranker,
    )
    ontology_root = selected.project_root / "ontology"
    ontology_profile = str(
        selected.get("ontology.domain_profile", "research-project")
    )
    if ontology_root.is_dir() and has_pending_release(ontology_root):
        # Heal a release journal left behind by a process that crashed
        # mid-materialization (see `kip.ontology_discovery_release`) before
        # the eager load below, so a half-applied two-file predicate release
        # never bricks every subsequent `OntologyCatalog.load`. When the
        # root is read-only, a pending journal cannot be healed here (or by
        # `materialize_ontology_release` next time either); fail loudly
        # instead of silently loading a possibly-inconsistent tree. The
        # common case (no journal) costs a single `Path.is_file` check and
        # never touches or creates the release lock file.
        if os.access(ontology_root, os.W_OK):
            complete_pending_release_locked(ontology_root)
        else:
            raise ConfigurationError(
                f"ontology root {ontology_root} is read-only and has a pending "
                "release journal; run from a writable checkout to heal it "
                "before loading"
            )
    ontology = (
        OntologyCatalog.load(ontology_root, domain_profile=ontology_profile)
        if ontology_root.is_dir()
        else None
    )
    egress = EgressPolicyUseCases(_build_egress_policy(selected, model_service_hosts))
    mining_config = selected.get("models.relation_mining", {}) or {}
    if not isinstance(mining_config, dict):
        raise ConfigurationError("models.relation_mining must be a table")
    selected_relation_miner = relation_miner
    if (
        load_models
        and selected_relation_miner is None
        and mining_config.get("enabled", False)
    ):
        if selected_generator is None:
            raise ConfigurationError(
                "enabled relation mining requires an enabled generation adapter"
            )
        if ontology is None:
            raise ConfigurationError(
                "enabled relation mining requires an ontology contract"
            )
        selected_relation_miner = GeneratorRelationMiner(
            selected_generator,
            ontology,
        )
    ontology_context_config = selected.get("ontology.answer_context", {}) or {}
    if not isinstance(ontology_context_config, dict):
        raise ConfigurationError("ontology.answer_context must be a table")
    knowledge = KnowledgeUseCases(
        selected_repository.knowledge,
        evidence,
        ontology,
    )
    auto_approve_config = selected.get("ontology.auto_approve", {}) or {}
    if not isinstance(auto_approve_config, dict):
        raise ConfigurationError("ontology.auto_approve must be a table")
    # Opt-in by default: an absent `[ontology.auto_approve]` section (or a
    # present section that omits `enabled`) must never silently promote
    # candidates without human review. A deployment must explicitly set
    # `enabled = true` after reviewing its predicate precision history.
    auto_approve_enabled = auto_approve_config.get("enabled", False)
    if not isinstance(auto_approve_enabled, bool):
        raise ConfigurationError("ontology.auto_approve.enabled must be boolean")
    ontology_rag = OntologyRagUseCases(
        selected_repository.knowledge,
        evidence,
        ontology,
        selected_repository.jobs,
        egress,
        selected_relation_miner,
        telemetry,
        knowledge=knowledge,
        max_mining_units=_bounded_integer(
            mining_config,
            "models.relation_mining",
            "max_units",
            default=200,
            minimum=1,
            maximum=500,
        ),
        max_mining_characters=_bounded_integer(
            mining_config,
            "models.relation_mining",
            "max_characters",
            default=480_000,
            minimum=1_000,
            maximum=2_000_000,
        ),
        max_entity_proposals=_bounded_integer(
            mining_config,
            "models.relation_mining",
            "max_entity_proposals",
            default=128,
            minimum=0,
            maximum=256,
        ),
        max_relation_proposals=_bounded_integer(
            mining_config,
            "models.relation_mining",
            "max_relation_proposals",
            default=256,
            minimum=0,
            maximum=512,
        ),
        auto_approve_enabled=auto_approve_enabled,
        # Bounds enforce the open-closed (0, 1] contract: a precision or
        # confidence floor of exactly 0 would let every candidate qualify
        # regardless of measured quality, defeating the gate.
        auto_approve_min_precision=_bounded_float(
            auto_approve_config,
            "ontology.auto_approve",
            "min_precision",
            default=0.95,
            minimum=0.0,
            maximum=1.0,
            exclusive_minimum=True,
        ),
        auto_approve_min_confidence=_bounded_float(
            auto_approve_config,
            "ontology.auto_approve",
            "min_confidence",
            default=0.8,
            minimum=0.0,
            maximum=1.0,
            exclusive_minimum=True,
        ),
        auto_approve_min_reviewed=_bounded_integer(
            auto_approve_config,
            "ontology.auto_approve",
            "min_reviewed",
            default=20,
            minimum=1,
            maximum=1_000_000,
        ),
    )
    ontology_context = OntologyContextUseCases(
        ontology_rag,
        knowledge,
        evidence,
        entity_limit=_bounded_integer(
            ontology_context_config,
            "ontology.answer_context",
            "entity_limit",
            default=16,
            minimum=1,
            maximum=50,
        ),
        edge_limit=_bounded_integer(
            ontology_context_config,
            "ontology.answer_context",
            "edge_limit",
            default=150,
            minimum=1,
            maximum=500,
        ),
        max_depth=_bounded_integer(
            ontology_context_config,
            "ontology.answer_context",
            "max_depth",
            default=4,
            minimum=1,
            maximum=8,
        ),
    )
    ontology_migration_config = selected.get("ontology.migrations", {}) or {}
    if not isinstance(ontology_migration_config, dict):
        raise ConfigurationError("ontology.migrations must be a table")
    ontology_migrations = OntologyMigrationUseCases(
        selected_repository.knowledge,
        evidence,
        domain_profile=ontology_profile,
        max_assertions=_bounded_integer(
            ontology_migration_config,
            "ontology.migrations",
            "max_assertions",
            default=10_000,
            minimum=1,
            maximum=1_000_000,
        ),
    )
    interaction_config = selected.get("interaction", {}) or {}
    if not isinstance(interaction_config, dict):
        raise ConfigurationError("interaction must be a table")
    interaction_enabled = interaction_config.get("enabled", False)
    if not isinstance(interaction_enabled, bool):
        raise ConfigurationError("interaction.enabled must be boolean")
    adaptive_discovery = selected.get("ontology.adaptive_discovery", False)
    if not isinstance(adaptive_discovery, bool):
        raise ConfigurationError("ontology.adaptive_discovery must be boolean")
    interactions = InteractionUseCases(
        selected_repository.interactions,
        enabled=interaction_enabled,
        discovery_enabled=adaptive_discovery,
        domain_profile=ontology_profile,
        clarification_ttl_seconds=_bounded_integer(
            interaction_config,
            "interaction",
            "clarification_ttl_seconds",
            default=3600,
            minimum=60,
            maximum=86_400,
        ),
        ontology_root=ontology_root if ontology_root.is_dir() else None,
    )
    application = Application(
        ingestion=IngestionUseCases(
            selected_repository.ingestion,
            selected_repository.jobs,
            sources,
            parsers,
            analyzer,
            LocalContentAddressedStore(selected.cas_path),
            source_files,
            selected_repository.evidence,
            minimum_quality_score=_bounded_float(
                selected.get("parsers", {}) or {},
                "parsers",
                "minimum_quality_score",
                default=0.70,
                minimum=0.0,
                maximum=1.0,
            ),
            deletion_grace_scans=_bounded_integer(
                _table(selected, "sync"),
                "sync",
                "deletion_grace_scans",
                default=2,
                minimum=1,
                maximum=100,
            ),
        ),
        retrieval=retrieval,
        evidence=evidence,
        knowledge=knowledge,
        operations=OperationsUseCases(
            selected,
            selected_repository.operations,
            selected_repository.jobs,
            selected_repository.retrieval,
            sources,
            parsers,
            selected_embedding,
        ),
        egress=egress,
        answering=AnsweringUseCases(
            selected,
            retrieval,
            evidence,
            egress,
            selected_generator,
            ontology_context,
            telemetry,
        ),
        ontology_rag=ontology_rag,
        ontology_context=ontology_context,
        ontology_migrations=ontology_migrations,
        telemetry=telemetry,
        interactions=interactions,
    )
    return Container(
        settings=selected,
        repository=selected_repository,
        application=application,
        embedding=selected_embedding,
        reranker=selected_reranker,
        lexical_reranker=selected_lexical_reranker,
        generator=selected_generator,
        relation_miner=selected_relation_miner,
        identity=selected_identity,
        trace_exporters=selected_trace_exporters,
        ontology=ontology,
    )


_LEXICAL_RERANKER_BACKENDS = frozenset({RerankerBackend.BM25, RerankerBackend.RAPIDFUZZ})


def _build_lexical_reranker(
    backend: RerankerBackend,
    config: Mapping[str, Any],
) -> RerankerPort:
    """A local, model-free reranker from a `models.reranker`-shaped table."""
    max_document_chars = int(config.get("max_document_chars", 8000))
    if backend is RerankerBackend.RAPIDFUZZ:
        return RapidFuzzRerankerAdapter(
            max_document_chars=max_document_chars,
            baseline_weight=float(config.get("baseline_weight", 0.15)),
        )
    return Bm25RerankerAdapter(
        max_document_chars=max_document_chars,
        k1=float(config.get("bm25_k1", 1.2)),
        b=float(config.get("bm25_b", 0.75)),
    )


def _build_generator(
    settings: Settings,
    raw: dict[str, object],
    *,
    allow_remote_egress: bool,
    model_service_hosts: tuple[str, ...] = (),
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
    kind = parse_generation_provider_kind(provider)
    match kind:
        case GenerationProviderKind.LOCAL:
            return OpenAICompatibleGenerationAdapter(
                base_url=str(raw.get("base_url", "http://127.0.0.1:7998")),
                api_key="",
                model=model,
                revision=revision,
                provider="local",
                allow_remote_egress=False,
                model_service_hosts=model_service_hosts,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        case GenerationProviderKind.OPENAI:
            api_key = _resolve_remote_generation_secret(settings, raw)
            return OpenAICompatibleGenerationAdapter(
                base_url=str(raw.get("base_url", "https://api.openai.com")),
                api_key=api_key,
                model=model,
                revision=revision,
                allow_remote_egress=allow_remote_egress,
                model_service_hosts=model_service_hosts,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        case GenerationProviderKind.ANTHROPIC:
            api_key = _resolve_remote_generation_secret(settings, raw)
            return AnthropicGenerationAdapter(
                base_url=str(raw.get("base_url", "https://api.anthropic.com")),
                api_key=api_key,
                model=model,
                revision=revision,
                allow_remote_egress=allow_remote_egress,
                model_service_hosts=model_service_hosts,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        case unreachable:
            assert_never(unreachable)


def _resolve_remote_generation_secret(settings: Settings, raw: dict[str, object]) -> str:
    secret_reference = str(raw.get("secret_ref", "")).strip()
    if not secret_reference:
        raise ConfigurationError("remote generation requires secret_ref")
    return settings.resolve_secret_reference(secret_reference)


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


def _model_service_hosts(settings: Settings) -> tuple[str, ...]:
    try:
        return normalize_model_service_hosts(
            str(host) for host in (settings.get("security.model_service_hosts", []) or [])
        )
    except ValueError as error:
        raise ConfigurationError(str(error)) from error


def _build_egress_policy(
    settings: Settings,
    model_service_hosts: tuple[str, ...] = (),
) -> EgressPolicy:
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
        model_service_hosts=model_service_hosts,
    )


def _table(settings: Settings, section: str) -> dict[str, object]:
    raw = settings.get(section, {}) or {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{section} must be a table")
    return raw


def _bounded_integer(
    raw: dict[str, object],
    section: str,
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(raw.get(name, default)))
    except ValueError as error:
        raise ConfigurationError(f"{section}.{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{section}.{name} must be between {minimum} and {maximum}"
        )
    return value


def _bounded_float(
    raw: dict[str, object],
    section: str,
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    try:
        value = float(str(raw.get(name, default)))
    except ValueError as error:
        raise ConfigurationError(f"{section}.{name} must be a number") from error
    if exclusive_minimum:
        if not minimum < value <= maximum:
            raise ConfigurationError(
                f"{section}.{name} must be greater than {minimum} and at most {maximum}"
            )
        return value
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{section}.{name} must be between {minimum} and {maximum}"
        )
    return value
