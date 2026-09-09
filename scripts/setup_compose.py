#!/usr/bin/env python3
"""Run the approved standalone Compose deployment without leaking secret files."""
from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import yaml

from kip.errors import ConfigurationError
from kip.settings import Settings


def main() -> int:
    root = Path(os.environ["PROJECT_ROOT"])
    config_path = root / "config/kip.generated.toml"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        compose = yaml.safe_load((root / "compose.generated.yaml").read_text(encoding="utf-8"))
        expected = {"mode": "standalone", "plan_fingerprint": config["setup"]["plan_fingerprint"]}
        if compose.get("x-kip-setup") != expected:
            raise ConfigurationError("generated Compose is outdated or mismatched; regenerate and apply a setup plan")
        environment = dict(os.environ)
        references = [config["database"]["secret_ref"]]
        references.extend(
            value for key, value in config["identity"].get("api_key", {}).items()
            if key in {"secret_ref", "admin_secret_ref"}
        )
        model_reference = config["models"]["generation"].get("secret_ref")
        if model_reference:
            references.append(model_reference)
        if sys.argv[1:] == ["down"]:
            # Compose still interpolates required values during teardown. This
            # branch can only stop services; never read expired/removed secrets
            # or require a database connection merely to remove containers.
            required_names = {"POSTGRES_PASSWORD", "KIP_CONTAINER_DATABASE_URL"}
            required_names.update(reference[4:] for reference in references if reference.startswith("env:"))
            for name in required_names:
                if not environment.get(name):
                    environment[name] = "unused-during-teardown"
        else:
            resolver = Settings.for_test()
            for reference in references:
                value = resolver.resolve_secret_reference(reference)
                if any(marker in value for marker in ("change-me-before-use", "replace-with-")):
                    raise ConfigurationError("replace example credentials before starting the deployment")
                if reference == "env:KIP_DATABASE_URL":
                    # Do not silently turn an existing external connection into
                    # the bundled database when the default name was reused.
                    selected = urlsplit(value)
                    if (
                        selected.scheme not in {"postgresql", "postgres"}
                        or selected.hostname not in {"localhost", "127.0.0.1", "::1"}
                        or (selected.port or 5432) != int(environment.get("KIP_POSTGRES_PORT", "5432"))
                        or selected.path.lstrip("/") != environment.get("POSTGRES_DB", "kip")
                        or unquote(selected.username or "") != environment.get("POSTGRES_USER", "kip_owner")
                        or unquote(selected.password or "") != environment.get("POSTGRES_PASSWORD", "")
                    ):
                        raise ConfigurationError("env:KIP_DATABASE_URL must match the bundled local PostgreSQL credentials and port; use a custom database secret reference for an external database")
                    username = quote(unquote(selected.username or ""), safe="")
                    password = quote(unquote(selected.password or ""), safe="")
                    environment["KIP_CONTAINER_DATABASE_URL"] = urlunsplit((
                        selected.scheme, f"{username}:{password}@postgres:5432",
                        selected.path, selected.query, "",
                    ))
                    continue
                if reference.startswith("env:"):
                    name = reference[4:]
                    environment[name] = value
                    environment.pop(f"{name}_FILE", None)
        return subprocess.run(
            ["docker", "compose", "-f", "compose.generated.yaml", "--profile", "app", *sys.argv[1:]],
            cwd=root, env=environment, check=False,
        ).returncode
    except (ConfigurationError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        # Resolver exceptions never include secret contents.
        print(f"setup runtime unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
