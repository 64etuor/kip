"""Read-only deployment configuration, for the one use case that reports on it.

Use cases are given the values they need, not a configuration object: search
gets :class:`kip.domain.configuration.SearchSettings`, answering gets
:class:`kip.domain.configuration.GenerationSettings`, and so on. `kip doctor`
is the exception, because reporting on the configuration *is* its use case —
it names the tables an operator wrote, including ones no other code reads. It
reaches them through this port, so the loader (`kip.settings`, which opens
TOML files and the environment) stays outside the application layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ConfigurationReader(Protocol):
    """The deployment's configuration as doctor reports it."""

    @property
    def project_root(self) -> Path:
        """The deployment root that every relative configured path resolves against."""
        ...

    @property
    def config_path(self) -> Path:
        """The config file this deployment loaded, whether or not it exists."""
        ...

    @property
    def cas_path(self) -> Path:
        """The content-addressed store directory."""
        ...

    @property
    def environment(self) -> str:
        """`development`, `test`, `production`, … — decides which checks are required."""
        ...

    def get(self, path: str, default: Any = None) -> Any:
        """The raw configured value at a dotted `path`, or `default`."""
        ...
