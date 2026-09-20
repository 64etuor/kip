"""Concrete probes behind :mod:`kip.ports.diagnostics`.

Thin wrappers over the adapters that already own each policy — the embedding
egress allowlist and the Kordoc version resolution — so a doctor check and the
adapter it reports on can never disagree.
"""

from __future__ import annotations

import httpx

from kip.adapters.embeddings.http import require_allowed_model_url
from kip.adapters.ocr.kordoc import (
    KordocOcrConfig,
    probe_kordoc_version,
    resolve_kordoc_expected_version,
)
from kip.ports.diagnostics import OcrRuntimeVersion

# Doctor-only timeouts: these probes must stay fast even when the adapters are
# configured generously for real embedding and OCR runs.
_MODEL_PROBE_TIMEOUT_SECONDS = 3.0


class HttpModelRuntimeProbe:
    """`GET {base_url}/models` through the model egress allowlist."""

    def served_model_ids(
        self,
        base_url: str,
        *,
        allow_remote_egress: bool,
        model_service_hosts: tuple[str, ...],
    ) -> list[str] | None:
        allowed = require_allowed_model_url(
            base_url,
            allow_remote_egress,
            model_service_hosts,
        )
        try:
            with httpx.Client(
                timeout=httpx.Timeout(_MODEL_PROBE_TIMEOUT_SECONDS), trust_env=False
            ) as client:
                response = client.get(f"{allowed}/models")
                if response.status_code != 200:
                    return None
                return sorted(str(row["id"]) for row in response.json()["data"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None


class KordocOcrRuntimeProbe:
    """The `version_argv` resolution :class:`KordocOcrAdapter` runs before OCR."""

    def version(
        self,
        *,
        argv: tuple[str, ...],
        version_argv: tuple[str, ...],
        expected_version: object,
        timeout_seconds: int,
    ) -> OcrRuntimeVersion:
        probe = probe_kordoc_version(
            KordocOcrConfig(
                argv=argv,
                version_argv=version_argv,
                expected_version=resolve_kordoc_expected_version(expected_version),
                timeout_seconds=timeout_seconds,
            )
        )
        return OcrRuntimeVersion(ok=probe.ok, version=probe.version, error=probe.error)
