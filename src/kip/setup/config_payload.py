from __future__ import annotations

from collections.abc import Iterable

from kip.domain.json_types import JsonObject, JsonValue
from kip.setup.models import SetupPlan


def _json_strings(values: Iterable[str]) -> list[JsonValue]:
    return [value for value in values]


def build_config_payload(plan: SetupPlan, *, container: bool) -> JsonObject:
    database: JsonObject = {
        "statement_timeout_ms": 15000,
        "secret_ref": plan.database_secret_ref.display(),
    }
    if plan.database_secret_ref.scheme == "env":
        database["url_env"] = plan.database_secret_ref.name
    model: JsonObject = {
        "enabled": plan.model_provider != "disabled",
        "provider": plan.model_provider,
        "allowed_classifications": _json_strings(plan.model_egress_classifications),
        "retention_policy": plan.model_retention_policy or "provider_default",
    }
    if plan.model_provider == "local":
        model["base_url"] = "http://127.0.0.1:7998"
    if plan.model_secret_ref is not None:
        model["secret_ref"] = plan.model_secret_ref.display()
        if plan.model_secret_ref.scheme == "env":
            model["api_key_env"] = plan.model_secret_ref.name
    sources: list[JsonValue] = [
        {
            "name": source.name,
            "root": source.target_root if container else source.host_root,
            "enabled": True,
            "read_only": True,
            "follow_symlinks": False,
            "include_extensions": _json_strings(source.include_extensions),
            "exclude_globs": _json_strings(source.exclude_globs),
            "acl_scope": source.acl_scope,
            "classification": source.classification,
        }
        for source in plan.sources
    ]
    identity: JsonObject = {
        "mode": plan.identity_mode,
        "owner": plan.identity_owner,
    }
    if plan.identity_mode == "proxy_jwt":
        identity["jwt"] = {
            "issuer": plan.jwt_issuer,
            "audience": plan.jwt_audience,
            "jwks_url": plan.jwt_jwks_url,
            "algorithms": ["RS256"],
            "principal_claim": "sub",
            "workspace_claim": "workspace",
            "group_claim": "groups",
            "scope_claim": "acl_scopes",
            "group_scope_prefix": "group:",
            "admin_groups": _json_strings(plan.jwt_admin_groups),
            "snapshot_id_claim": "acl_snapshot_id",
            "snapshot_version_claim": "acl_snapshot_version",
            "snapshot_captured_at_claim": "acl_snapshot_captured_at",
            "snapshot_expires_at_claim": "acl_snapshot_expires_at",
            "jwks_cache_seconds": 300,
            "jwks_timeout_seconds": 5,
            "clock_skew_seconds": 30,
        }
    else:
        api_key: JsonObject = {
            "principal_id": "bootstrap-operator",
            "acl_scopes": [f"workspace:{plan.workspace}"],
        }
        if plan.identity_api_key_secret_ref is not None:
            api_key["secret_ref"] = plan.identity_api_key_secret_ref.display()
            if plan.identity_api_key_secret_ref.scheme == "env":
                api_key["api_key_env"] = plan.identity_api_key_secret_ref.name
        if plan.identity_admin_key_secret_ref is not None:
            api_key["admin_secret_ref"] = plan.identity_admin_key_secret_ref.display()
            if plan.identity_admin_key_secret_ref.scheme == "env":
                api_key["admin_key_env"] = plan.identity_admin_key_secret_ref.name
        identity["api_key"] = api_key
    return {
        "app": {
            "environment": "production",
            "workspace": plan.workspace,
            "log_level": "INFO",
        },
        "setup": {
            "plan_fingerprint": plan.plan_fingerprint,
            "source_ownership": plan.source_ownership,
        },
        "database": database,
        "storage": {
            "cas_path": "/var/lib/kip/cas" if container else plan.cas_path
        },
        "api": {
            "host": "0.0.0.0" if container else "127.0.0.1",
            "port": 8080,
            "max_request_bytes": 10 * 1024 * 1024,
        },
        "identity": identity,
        "security": {
            "allow_remote_model_egress": plan.model_provider != "disabled",
            "follow_symlinks": False,
        },
        "telemetry": {
            "query_traces_enabled": True,
            "retention_days": plan.retention_days,
            "otel": {
                "enabled": False,
                "service_name": "kip",
                "endpoint": "http://otel-collector:4318",
            },
        },
        "search": {
            "semantic_enabled": False,
            "default_mode": "reranked",
            "context_max_chars": 120000,
        },
        "models": {
            "generation": model,
            "relation_mining": {
                "enabled": plan.relation_mining_mode == "enabled",
                "max_units": 200,
                "max_characters": 480000,
                "max_entity_proposals": 128,
                "max_relation_proposals": 256,
            },
            "reranker": {
                "enabled": True,
                "backend": "bm25",
                "max_document_chars": 8000,
                "baseline_weight": 0.15,
            },
        },
        "parsers": {
            "parser_timeout_seconds": 120,
            "minimum_quality_score": 0.70,
            "shadow_parse_critical_documents": True,
            "isolation": {
                "enabled": True,
                "wall_seconds": 180,
                "cpu_seconds": 120,
                "memory_mib": 6144,
                "result_mib": 256,
                "diagnostic_kib": 16,
                "cpu_threads": 4,
                "nice": 5,
            },
            "ocr": {
                "timeout_seconds": 120,
                "kordoc": {
                    "enabled": True,
                    "argv": ["kordoc", "--format", "json", "--ocr", "--silent"],
                    "version_argv": ["kordoc", "--version"],
                    "expected_version": "4.7.3",
                },
                "pptx": {
                    "max_images": 128,
                    "max_image_bytes": 20 * 1024 * 1024,
                    "max_total_bytes": 100 * 1024 * 1024,
                    "min_width_px": 96,
                    "min_height_px": 48,
                },
            },
            "hwp": {
                "order": ["hwp-hwpx-parser", "kordoc", "unhwp", "paired_pdf"],
                "hwp-hwpx-parser": {
                    "enabled": True,
                    "max_chars_per_unit": 4000,
                },
                "kordoc": {
                    "enabled": False,
                    "argv": ["kordoc", "{input}", "--format", "json"],
                },
                "unhwp": {
                    "enabled": False,
                    "argv": [
                        "unhwp",
                        "convert",
                        "{input}",
                        "-o",
                        "{output_dir}",
                        "--all",
                    ],
                },
            },
        },
        "sources": {"filesystem": sources},
        "operations": {
            "backup_path": (
                "/var/lib/kip/backups" if container else plan.backup_path
            ),
            "retention_days": plan.retention_days,
            "sync_schedule": plan.sync_schedule,
        },
        "evaluation": {"dataset": plan.evaluation_dataset},
        "ontology": {
            "domain_profile": plan.ontology_profile,
            "adaptive_discovery": plan.interaction_memory_mode == "explicit_consent",
            "reviewers": _json_strings(plan.ontology_reviewers),
            "auto_approve": {"enabled": False},
        },
        "interaction": {
            "enabled": plan.interaction_memory_mode == "explicit_consent",
            "clarification_ttl_seconds": 3600,
        },
    }
