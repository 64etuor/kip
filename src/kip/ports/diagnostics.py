"""Runtime probes `kip doctor` needs from outside the process.

The diagnostics use cases decide what a probe result means; these ports are
the only way they reach a model runtime or an OCR binary, so the application
layer never imports the adapters that speak HTTP or spawn subprocesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ModelRuntimeProbe(Protocol):
    """Reads what an OpenAI-compatible model runtime advertises."""

    def served_model_ids(
        self,
        base_url: str,
        *,
        allow_remote_egress: bool,
        model_service_hosts: tuple[str, ...],
    ) -> list[str] | None:
        """Sorted model ids served at `base_url`, or None when unreachable.

        An empty list means "reachable, serving nothing", which is a different
        operator action from an unreachable runtime. The probe applies the same
        egress allowlist the model adapters enforce and raises
        :class:`kip.errors.ConfigurationError` for a URL that policy refuses,
        so doctor never connects to a host a query would refuse.
        """
        ...


@dataclass(frozen=True, slots=True)
class OcrRuntimeVersion:
    """Result of resolving and version-checking a configured OCR runtime.

    ``ok`` is True only when the runtime resolved and reported the expected
    version. ``version`` is the detected version when one could be parsed
    (even on failure, so a mismatch can be reported), and ``error`` carries an
    actionable reason whenever ``ok`` is False.
    """

    ok: bool
    version: str | None
    error: str | None


class OcrRuntimeProbe(Protocol):
    """Resolves the configured OCR runtime without raising."""

    def version(
        self,
        *,
        argv: tuple[str, ...],
        version_argv: tuple[str, ...],
        expected_version: object,
        timeout_seconds: int,
    ) -> OcrRuntimeVersion:
        """Apply the adapter's own resolution policy to a raw OCR config.

        ``expected_version`` is the unresolved configuration value: the
        adapter owns the mapping from a preserved deployment pin to the
        version it enforces, so doctor and the parser cannot disagree.
        """
        ...
