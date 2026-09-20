"""The `kip doctor` engine CLI and MCP share.

Each check answers one operator question and returns a
`{name, ok, required, details}` dict; `required` marks the checks whose
failure means the deployment cannot answer at all. Runtime probes that leave
the process (a model runtime, an OCR binary) arrive through
:mod:`kip.ports.diagnostics`, so this layer holds only the verdicts.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kip.application.operations import OperationsUseCases
from kip.application.search import RetrievalUseCases
from kip.database_port import database_port_doctor_check
from kip.domain.models import Capabilities, RequestContext
from kip.errors import ConfigurationError, KipError
from kip.ports.configuration import ConfigurationReader
from kip.ports.diagnostics import ModelRuntimeProbe, OcrRuntimeProbe
from kip.ports.ontology import OntologyReleaseWriterPort
from kip.skill_installs import STALE, skill_install_statuses

# A doctor-only timeout: this check must stay fast even when
# parsers.ocr.timeout_seconds is configured generously for real OCR runs.
_KORDOC_DOCTOR_PROBE_TIMEOUT_SECONDS = 5


def _served_model_ids(
    probe: ModelRuntimeProbe, settings: ConfigurationReader, base_url: str
) -> list[str] | None:
    """Model ids the runtime serves, or ``None`` when it is not reachable.

    Probes through the same egress allowlist the adapters enforce, so doctor
    never connects to a host a query would refuse; a rejected URL raises
    ``ConfigurationError`` for the caller to report as its own reason. An
    empty list means "reachable, serving nothing", which is a different
    operator action from an unreachable runtime.
    """
    return probe.served_model_ids(
        base_url,
        allow_remote_egress=bool(settings.get("security.allow_remote_model_egress", False)),
        model_service_hosts=tuple(
            str(host) for host in (settings.get("security.model_service_hosts", []) or [])
        ),
    )


def _http_reranker_doctor_reason(
    probe: ModelRuntimeProbe,
    settings: ConfigurationReader,
    embedding_base_url: str,
    embedding_served: list[str] | None,
) -> str | None:
    """Why the configured cross-encoder cannot answer, if it cannot.

    Only ``models.reranker.backend = "http"`` takes a model name from a
    runtime; every other backend runs in-process and cannot be swapped under
    its name. Reuses the embedding probe when both point at the same runtime.
    """
    config = settings.get("models.reranker", {}) or {}
    if not isinstance(config, dict) or not config.get("enabled", False):
        return None
    if str(config.get("backend", "bm25")) != "http":
        return None
    model = str(config.get("model", ""))
    if not model:
        return None
    base_url = str(config.get("base_url", "http://127.0.0.1:7997")).rstrip("/")
    try:
        served = (
            embedding_served
            if base_url == embedding_base_url
            else _served_model_ids(probe, settings, base_url)
        )
    except ConfigurationError as error:
        return f"models.reranker.base_url is not usable: {error}; reranked mode fails until it is fixed"
    if served is None:
        return (
            f"reranker model runtime not reachable at {base_url}; reranked mode fails "
            "and default search keeps the lexical reranker until it is started"
        )
    if model not in served:
        return (
            f"the model runtime at {base_url} serves {served}, not the configured "
            f"models.reranker.model {model!r}; start it with that model "
            "(KIP_SEMANTIC_RERANKER=on, and KIP_RERANKER_SERVED_MODEL names what it advertises) "
            "or fix models.reranker.model. Reranked mode fails until then"
        )
    return None


def _semantic_doctor_check(
    probe: ModelRuntimeProbe,
    settings: ConfigurationReader,
    capabilities: Capabilities,
    verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report whether default search really uses the vector channel (ADR-065).

    Not required: search still answers through the lexical path when the
    model runtime is down or the projection is not active yet, so this is a
    WARN-level signal with the exact command that finishes the setup.
    """
    if not capabilities.semantic_search_configured:
        # Intentional lexical-only mode, not a fault: say so explicitly so
        # `enabled: false` is not read as something to repair.
        # Only the configuration disables it; KIP_SEMANTIC is read at install and
        # plan time, never here, so it cannot be named as the cause.
        disabled_by = (
            "search.semantic_enabled is not set (defaults to false)"
            if settings.get("search.semantic_enabled") is None
            else "search.semantic_enabled = false (set by configuration; KIP_SEMANTIC=off at install writes this)"
        )
        return {
            "name": "semantic_search",
            "ok": True,
            "required": False,
            "details": {
                "enabled": False,
                "model_runtime": None,
                "projection": "disabled",
                "reason": None,
                "state": "disabled_by_configuration",
                "disabled_by": disabled_by,
                "message": (
                    f"semantic search is disabled by configuration: {disabled_by}; "
                    "lexical search is the intended mode"
                ),
            },
        }
    embedding = settings.get("models.embedding", {}) or {}
    base_url = str(embedding.get("base_url", "http://127.0.0.1:7997")).rstrip("/")
    served: list[str] | None = None
    egress_reason: str | None = None
    if isinstance(embedding, dict) and embedding.get("enabled", False):
        try:
            served = _served_model_ids(probe, settings, base_url)
        except ConfigurationError as error:
            egress_reason = f"models.embedding.base_url is not usable: {error}"
    runtime_ok = served is not None
    configured_model = str(embedding.get("model", "")) if isinstance(embedding, dict) else ""
    projection = capabilities.semantic_projection_status
    reason: str | None = None
    if not isinstance(embedding, dict) or not embedding.get("enabled", False):
        reason = "search.semantic_enabled is on but models.embedding is disabled; enable it or turn semantic search off"
    elif egress_reason is not None:
        reason = egress_reason
    elif not runtime_ok:
        reason = (
            f"model runtime not reachable at {base_url}; start it with ./scripts/semantic-server.sh start "
            "(or install it with ./scripts/bootstrap-semantic.sh && ./scripts/semantic-server.sh prefetch). "
            "Search falls back to lexical until then"
        )
    elif configured_model and served == []:
        reason = (
            f"the model runtime at {base_url} answers but serves no model; it is still loading "
            "or was started without one. Wait with ./scripts/semantic-server.sh wait, or start it "
            "again. Search falls back to lexical until then"
        )
    elif configured_model and served is not None and configured_model not in served:
        # Reachable but advertising another name (KIP_EMBEDDING_SERVED_MODEL
        # pointing elsewhere, a stale unit file, an edited models.embedding.model):
        # queries would be embedded in another space, so the adapter refuses
        # and search degrades to lexical.
        reason = (
            f"the model runtime at {base_url} serves {served}, not the configured "
            f"models.embedding.model {configured_model!r}; start it with that model "
            "(KIP_EMBEDDING_SERVED_MODEL names what it advertises) or fix "
            "models.embedding.model. Search falls back to lexical until then. Only the served "
            "name is compared: /models exposes no revision or weight hash, so "
            "models.embedding.revision stays unverified at runtime"
        )
    elif projection == "incompatible":
        reason = (
            "another embedding space is active. When both spaces are release-reviewed and "
            "search.semantic_auto_activate is on, a complete ./scripts/kip projection rebuild --name semantic "
            "switches automatically; a space outside the release-reviewed identities is never replaced "
            "automatically, so rebuild, evaluate, then run ./scripts/kip projection activate "
            "--report REPORT --candidate VARIANT"
        )
    elif not capabilities.semantic_search:
        reason = (
            f"semantic projection is {projection}; run ./scripts/kip sync run --source SOURCE "
            "or ./scripts/kip projection rebuild --name semantic to embed and activate it"
        )
    elif verification is not None and verification.get("ok") is not True:
        projection = "stale"
        reason = (
            f"the active projection holds {verification.get('indexed_units')} of "
            f"{verification.get('content_units')} visible units; run ./scripts/kip sync run "
            "--source SOURCE or ./scripts/kip projection rebuild --name semantic"
        )
    else:
        # Last: the cross-encoder only affects `reranked` mode, so a projection
        # or runtime problem above is the more useful thing to report first.
        reason = _http_reranker_doctor_reason(probe, settings, base_url, served)
    return {
        "name": "semantic_search",
        "ok": reason is None,
        "required": False,
        "details": {
            "enabled": True,
            "model_runtime": runtime_ok,
            "projection": projection,
            "reason": reason,
            "state": "ready" if reason is None else "degraded",
        },
    }


def _version_key(version: str) -> tuple[int, ...] | None:
    parts = version.split(".")
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else None


def _postgres_extensions_doctor_check(operations: Any) -> dict[str, Any]:
    """Compare the `vector` catalog version with the server's pgvector.

    Not required: search keeps working on an outdated catalog, but backups
    record `extversion` and `ALTER EXTENSION vector UPDATE` is what brings it
    level. Image and Compose pins are outside doctor; it reports both versions.
    """
    details: dict[str, Any] = {
        "extension": "vector", "installed_version": None, "server_version": None,
        "state": "not_applicable", "reason": None,
    }
    check = {"name": "postgres_extensions", "ok": True, "required": False, "details": details}
    try:
        versions = operations.extension_versions("vector")
    except Exception as error:
        # A diagnostic must never fail doctor: an unreadable catalog is "not checked".
        details.update({"state": "not_checked", "error": str(error)})
        return check
    if versions is None:
        return check
    if not isinstance(versions, tuple) or len(versions) != 2:
        details.update({"state": "not_checked", "error": f"unexpected extension_versions result: {versions!r}"})
        return check
    installed, server = versions
    details.update({"installed_version": installed, "server_version": server})
    if installed is not None and server is None:
        # Installed in this database, but the server cannot load it (an image
        # without pgvector). The older adapter shape reports (None, None) here.
        check["ok"] = False
        details.update({
            "state": "installed_but_unavailable",
            "reason": f"the vector extension is installed ({installed}) but this server has no pgvector",
            "fix": "run the pgvector PostgreSQL image the database was created with",
        })
        return check
    if installed is None or server is None:
        details["state"] = "not_installed" if server is not None else "not_available"
        return check
    if installed == server:
        details["state"] = "current"
        return check
    installed_key, server_key = _version_key(installed), _version_key(server)
    if installed_key is None or server_key is None:
        # A development or distribution build (`0.8.7-dev`) cannot be ordered and
        # may be newer, so report both versions without migrate advice.
        details["state"] = "unknown_version"
        return check
    check["ok"] = False
    if installed_key > server_key:
        details.update({
            "state": "newer_than_server",
            "reason": (
                f"the vector extension catalog ({installed}) is newer than the server's pgvector "
                f"({server}), which happens when the database runs on an older image than the one "
                "migrations ran on"
            ),
            "fix": "run the PostgreSQL image the database was migrated with",
        })
    else:
        details.update({
            "state": "outdated",
            "reason": (
                f"the vector extension catalog ({installed}) is older than the server's pgvector "
                f"({server}), which happens when migrations ran before the image changed"
            ),
            "fix": "./scripts/kip migrate",
        })
    return check


def _database_port_doctor_check(project_root: Path) -> dict[str, Any]:
    """`kip_database_port_check` in scripts/common.sh, reported instead of refused.

    `scripts/kip` skips that guard for doctor so this check can show the
    problem. Required when it applies: a loopback URL on another port reaches
    whichever PostgreSQL owns that port, possibly another deployment's. Unlike
    the bash wrapper, this also reads `KIP_DATABASE_URL_FILE` when the env var
    is empty. A URL port that is not a number from 1 to 65535 fails here:
    that URL cannot connect to anything.
    """
    return database_port_doctor_check(project_root)


def _kordoc_ocr_doctor_check(probe: OcrRuntimeProbe, settings: ConfigurationReader) -> dict[str, Any]:
    """Report whether the configured Kordoc OCR runtime is resolvable.

    Reuses :class:`OcrRuntimeProbe`, the same version-resolution policy
    the OCR adapter enforces before every ``recognize`` call, so this check
    and the adapter can never disagree about what counts as a usable Kordoc
    runtime. Not required: parsing still succeeds (in degraded ``partial``
    mode with ``OCR_FAILED`` warnings) without it, so this is a WARN-level
    signal that surfaces the degradation before a real sync hits it.
    """
    kordoc_config = settings.get("parsers.ocr.kordoc", {}) or {}
    enabled = isinstance(kordoc_config, dict) and bool(kordoc_config.get("enabled", False))
    if not enabled:
        return {
            "name": "kordoc_ocr_resolvable",
            "ok": True,
            "required": False,
            "details": {"enabled": False, "version": None, "reason": None},
        }
    resolved = probe.version(
        argv=tuple(str(item) for item in kordoc_config.get("argv", [])),
        version_argv=tuple(str(item) for item in kordoc_config.get("version_argv", [])),
        expected_version=kordoc_config.get("expected_version"),
        timeout_seconds=_KORDOC_DOCTOR_PROBE_TIMEOUT_SECONDS,
    )
    reason = (
        None
        if resolved.ok
        else (
            f"kordoc enabled but not resolvable on PATH ({resolved.error}); "
            "image-bearing PDF/PPTX will degrade to partial; run "
            "scripts/install-kordoc.sh or disable parsers.ocr.kordoc"
        )
    )
    return {
        "name": "kordoc_ocr_resolvable",
        "ok": resolved.ok,
        "required": False,
        "details": {"enabled": True, "version": resolved.version, "reason": reason},
    }


def _doctor_summary(checks: list[dict[str, Any]], required_failures: list[str]) -> str:
    """One-line Korean plain-language verdict for the `doctor` payload.

    `doctor`'s `checks` list (`content_units`, `lexical_units`,
    `semantic_projection_status`-shaped detail dicts, ...) is meaningful to
    an operator who already knows the system, not to a non-expert. This is
    a plain `dict` field (not a versioned contract model), so adding it
    here does not need a contract regeneration.
    """
    required_total = sum(1 for item in checks if item["required"])
    required_ok = required_total - len(required_failures)
    if required_failures:
        return (
            f"문제: 필수 점검 {len(required_failures)}건 실패 ({', '.join(required_failures)}). "
            "아래 checks 항목의 details.reason과 details.fix를 확인하세요."
        )
    lexical_note = ""
    for item in checks:
        details = item.get("details")
        if (
            item["name"] == "semantic_search" and isinstance(details, dict)
            and details.get("state") == "disabled_by_configuration"
        ):
            lexical_note = (
                f" 시맨틱 검색은 설정으로 꺼져 있습니다: {details.get('disabled_by')}. "
                "lexical 검색이 의도된 모드입니다."
            )
    optional_warnings = [item for item in checks if not item["required"] and not item["ok"]]
    if not optional_warnings:
        return f"정상: 필수 점검 {required_ok}/{required_total} 통과.{lexical_note}"
    first = optional_warnings[0]
    details = first.get("details")
    reason = details.get("reason") if isinstance(details, dict) else None
    hint = f"{first['name']}" + (f" — {reason}" if reason else "")
    return (
        f"정상: 필수 점검 {required_ok}/{required_total} 통과. "
        f"경고 {len(optional_warnings)}건({hint}).{lexical_note}"
    )


def _doctor_summary_en(checks: list[dict[str, Any]], required_failures: list[str]) -> str:
    """English twin of `_doctor_summary` for agents following English skills."""
    required_total = sum(1 for item in checks if item["required"])
    required_ok = required_total - len(required_failures)
    if required_failures:
        return (
            f"Problem: {len(required_failures)} required check(s) failed "
            f"({', '.join(required_failures)}). Read checks[].details.reason "
            "and details.fix for the next command."
        )
    lexical_note = ""
    for item in checks:
        details = item.get("details")
        if (
            item["name"] == "semantic_search" and isinstance(details, dict)
            and details.get("state") == "disabled_by_configuration"
        ):
            lexical_note = (
                f" Semantic search is off by configuration: {details.get('disabled_by')}. "
                "Lexical search is the intended mode."
            )
    optional_warnings = [item for item in checks if not item["required"] and not item["ok"]]
    if not optional_warnings:
        return f"OK: {required_ok}/{required_total} required checks passed.{lexical_note}"
    first = optional_warnings[0]
    details = first.get("details")
    reason = details.get("reason") if isinstance(details, dict) else None
    hint = f"{first['name']}" + (f" — {reason}" if reason else "")
    return (
        f"OK: {required_ok}/{required_total} required checks passed. "
        f"{len(optional_warnings)} warning(s) ({hint}).{lexical_note}"
    )


def _mcp_registration_doctor_check(settings: ConfigurationReader) -> dict[str, Any]:
    """Warn when the deployment's `.mcp.json` only works from the deployment root.

    Setup used to write `bash scripts/mcp.sh` with a relative KIP_CONFIG, and
    upgrades preserve that file. An MCP client starts the server from its own
    working directory, where the relative script does not exist. The file is
    the operator's, so doctor reports the absolute replacement and never
    rewrites it.
    """
    root = settings.project_root
    path = root / ".mcp.json"
    details: dict[str, Any] = {"path": str(path), "relative_paths": [], "reason": None}
    try:
        server = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["kip"]
    except (OSError, ValueError, TypeError, KeyError):
        server = None
    if not isinstance(server, dict):
        return {"name": "mcp_registration", "ok": True, "required": False, "details": details}

    def absolute(value: str) -> str:
        return os.path.normpath(root / value)

    def relative_path(value: object) -> bool:
        # Values with `$` or whitespace are client-expanded or shell snippets;
        # rewriting them against the root would produce a broken replacement.
        return (
            isinstance(value, str) and "/" in value
            and not any(character in value for character in "$ \t")
            and not value.startswith(("-", "~")) and not Path(value).is_absolute()
        )

    replacement = dict(server)
    relative: list[str] = []
    command = server.get("command")
    if relative_path(command):
        relative.append(str(command))
        replacement["command"] = absolute(str(command))
    args = server.get("args")
    if isinstance(args, list):
        relative.extend(str(item) for item in args if relative_path(item))
        replacement["args"] = [absolute(item) if relative_path(item) else item for item in args]
    environment = server.get("env")
    config = environment.get("KIP_CONFIG") if isinstance(environment, dict) else None
    # A relative KIP_CONFIG alone is harmless: `common.sh` exports the
    # deployment as KIP_PROJECT_ROOT and settings resolve it there. It only
    # travels with a relative script, which is what fails elsewhere.
    if relative and isinstance(environment, dict) and isinstance(config, str) and relative_path(config):
        relative.append(config)
        replacement["env"] = {**environment, "KIP_CONFIG": absolute(config)}
    if not relative:
        return {"name": "mcp_registration", "ok": True, "required": False, "details": details}
    details.update({
        "relative_paths": relative,
        "reason": (
            ".mcp.json registers kip with relative paths, so an MCP client started "
            "outside the deployment root fails with 'No such file or directory'"
        ),
        "fix": (
            f"Edit {path} by hand (doctor never rewrites it): set mcpServers.kip to "
            "details.replacement, which uses absolute paths. With the kip launcher on "
            "PATH, the entry {\"command\": \"kip\", \"args\": [\"mcp\"]} also works from "
            "any directory."
        ),
        "replacement": replacement,
    })
    return {"name": "mcp_registration", "ok": False, "required": False, "details": details}


def _skill_installs_doctor_check(settings: ConfigurationReader) -> dict[str, Any]:
    """Warn when an agent skill copy this deployment installed is out of date.

    Only `stale` fails the check: a removed location or one another deployment
    now owns is reported, but upgrades already skip those and doctor must not
    warn forever about a project the operator deleted.
    """
    try:
        statuses = skill_install_statuses(settings.project_root)
    except ValueError as exc:
        return {
            "name": "skill_installs", "ok": False, "required": False,
            "details": {"installs": [], "reason": str(exc)},
        }
    installs = [
        {
            "destination": str(status.destination),
            "client": status.client,
            "scope": status.scope,
            "skill": status.skill,
            "state": status.state,
            "installed_version": status.installed_version,
            "deployment_version": status.deployment_version,
        }
        for status in statuses
    ]
    stale = [item for item in installs if item["state"] == STALE]
    details: dict[str, Any] = {"installs": installs, "reason": None}
    if stale:
        details["reason"] = (
            "agent skill copies were installed from another KIP version and may "
            "give agents outdated instructions"
        )
        details["fix"] = "./scripts/install-agent-files.sh --refresh"
    return {"name": "skill_installs", "ok": not stale, "required": False, "details": details}


class DiagnosticsUseCases:
    """Collects the deployment diagnostics every edge reports identically."""

    def __init__(
        self,
        settings: ConfigurationReader,
        operations: OperationsUseCases,
        retrieval: RetrievalUseCases,
        model_runtime: ModelRuntimeProbe,
        ocr_runtime: OcrRuntimeProbe,
        release_writer: OntologyReleaseWriterPort,
    ) -> None:
        self._settings = settings
        self._operations = operations
        self._retrieval = retrieval
        self._model_runtime = model_runtime
        self._ocr_runtime = ocr_runtime
        self._release_writer = release_writer

    def collect_doctor_report(self, context: RequestContext) -> dict[str, Any]:
        """The doctor payload CLI and MCP share, including `summary` and `summary_en`."""
        settings = self._settings
        capabilities = self._operations.capabilities(context)
        checks: list[dict[str, Any]] = []
        checks.append(
            {
                "name": "configuration",
                "ok": settings.config_path.exists(),
                "required": settings.environment not in {"development", "test"},
                "details": {"path": str(settings.config_path)},
            }
        )
        checks.append(
            {
                "name": "canonical_repository",
                "ok": capabilities.repository in {"postgresql", "memory"},
                "required": True,
                "details": {"backend": capabilities.repository},
            }
        )
        checks.append(_database_port_doctor_check(settings.project_root))
        checks.append(_postgres_extensions_doctor_check(self._operations))
        checks.append(
            {
                "name": "content_addressed_store",
                "ok": settings.cas_path.exists() and settings.cas_path.is_dir(),
                "required": True,
                "details": {"path": str(settings.cas_path)},
            }
        )
        checks.append(_kordoc_ocr_doctor_check(self._ocr_runtime, settings))
        verification = None
        if capabilities.semantic_search:
            try:
                verification = self._retrieval.verify_semantic_projection(context)
            except KipError:
                verification = None
        checks.append(
            _semantic_doctor_check(
                self._model_runtime, settings, capabilities, verification
            )
        )
        for source in settings.get("sources.filesystem", []) or []:
            if not isinstance(source, dict) or not source.get("enabled", True):
                continue
            root_value = source.get("root")
            root_path = Path(str(root_value)).expanduser().resolve() if root_value else None
            checks.append(
                {
                    "name": f"filesystem_source:{source.get('name', 'unnamed')}",
                    "ok": bool(root_path and root_path.exists() and root_path.is_dir()),
                    "required": True,
                    "details": {
                        "path": str(root_path) if root_path else None,
                        "configured_read_only": bool(source.get("read_only", False)),
                    },
                }
            )
        ontology_root = settings.project_root / "ontology"
        adaptive_discovery = bool(settings.get("ontology.adaptive_discovery", False))
        ontology_root_writable = ontology_root.is_dir() and os.access(ontology_root, os.W_OK)
        ontology_check_ok = (not adaptive_discovery) or ontology_root_writable
        checks.append(
            {
                "name": "ontology_adaptive_discovery_writable",
                "ok": ontology_check_ok,
                "required": adaptive_discovery,
                "details": {
                    "path": str(ontology_root),
                    "adaptive_discovery_enabled": adaptive_discovery,
                    "reason": None
                    if ontology_check_ok
                    else (
                        "ontology.adaptive_discovery is enabled but the ontology "
                        "root is missing or not writable; discovery approval "
                        "will fail closed"
                    ),
                },
            }
        )
        pending_release_path = self._release_writer.pending_release_journal(ontology_root)
        pending_release_exists = pending_release_path.exists()
        checks.append(
            {
                "name": "ontology_pending_release_journal",
                "ok": not pending_release_exists,
                "required": False,
                "details": {
                    "path": str(pending_release_path),
                    "reason": None
                    if not pending_release_exists
                    else (
                        "a pending ontology release journal was found; a prior "
                        "release may have crashed mid-write and will be healed "
                        "on the next container start-up or materialization"
                    ),
                },
            }
        )
        checks.append(_mcp_registration_doctor_check(settings))
        checks.append(_skill_installs_doctor_check(settings))
        required_failures = [item["name"] for item in checks if item["required"] and not item["ok"]]
        return {
            "healthy": not required_failures,
            "required_failures": required_failures,
            "checks": checks,
            "capabilities": capabilities.model_dump(mode="json"),
            "summary": _doctor_summary(checks, required_failures),
            "summary_en": _doctor_summary_en(checks, required_failures),
        }
