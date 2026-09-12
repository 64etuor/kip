#!/usr/bin/env python3
"""Run the approved standalone Compose deployment without leaking secret files."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import yaml

from kip.errors import ConfigurationError
from kip.settings import Settings

# Every ${NAME:?...} / ${NAME?...} in the generated file: Compose refuses to
# parse the project when one of them is unset. Deriving the set from the file
# keeps teardown working when the template gains a required variable, as it did
# with the kip_api / kip_worker / kip_backup login passwords the `roles`
# service and the API and worker database URLs now interpolate.
_REQUIRED_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")


def main() -> int:
    root = Path(os.environ["PROJECT_ROOT"])
    config_path = root / "config/kip.generated.toml"
    database_only = sys.argv[1:] == ["--database-only"]
    if "--database-only" in sys.argv[1:] and not database_only:
        print("--database-only does not accept additional arguments", file=sys.stderr)
        return 2
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        compose_text = (root / "compose.generated.yaml").read_text(encoding="utf-8")
        compose = yaml.safe_load(compose_text)
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
            required_names = set(_REQUIRED_INTERPOLATION.findall(compose_text))
            required_names.update(reference[4:] for reference in references if reference.startswith("env:"))
            for name in required_names:
                if not environment.get(name):
                    environment[name] = "unused-during-teardown"
        else:
            resolver = Settings.for_test()
            for reference in references[:1] if database_only else references:
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
        if database_only:
            host_config_path = root / "config/kip.host.generated.toml"
            with host_config_path.open("rb") as handle:
                host_config = tomllib.load(handle)
            if (
                host_config["setup"]["plan_fingerprint"] != config["setup"]["plan_fingerprint"]
                or host_config["database"]["secret_ref"] != config["database"]["secret_ref"]
            ):
                raise ConfigurationError("generated host config is mismatched; regenerate and apply a setup plan")
            # Compose interpolates every service, including the API and worker
            # that this mode never starts. Migration also loads identity settings.
            # Their credentials are deliberately neither read nor required here.
            database_ref = config["database"]["secret_ref"]
            database_name = database_ref[4:] if database_ref.startswith("env:") else None
            identity_keys = host_config.get("identity", {}).get("api_key", {})
            unused_names = {reference[4:] for reference in references[1:] if reference.startswith("env:")}
            unused_names.update({
                identity_keys.get("api_key_env", "KIP_API_KEY"),
                identity_keys.get("admin_key_env", "KIP_ADMIN_KEY"),
            })
            for name in unused_names - {database_name}:
                environment[name] = "unused-during-database-only"
                environment.pop(f"{name}_FILE", None)
            environment["KIP_CONFIG"] = str(host_config_path)
            environment["KIP_PROJECT_ROOT"] = str(root)
            # common.sh already loaded this deployment. Do not restore an unused
            # *_FILE from .env alongside its migration-only placeholder.
            environment["KIP_SKIP_DOTENV"] = "1"
            if "postgres" in compose["services"]:
                result = subprocess.run(
                    ["docker", "compose", "-f", "compose.generated.yaml", "--profile", "app",
                     "up", "-d", "--wait", "--wait-timeout", "60", "postgres"],
                    cwd=root, env=environment, check=False,
                )
                if result.returncode:
                    return result.returncode
            migrated = subprocess.run(
                [str(root / "scripts/migrate.sh")], cwd=root, env=environment, check=False,
            ).returncode
            if migrated or "roles" not in compose["services"]:
                return migrated
            # The application roles belong to the database, not to the app
            # profile: backup connects as kip_backup, and a later app-up.sh
            # expects the kip_api and kip_worker logins to exist. The grants
            # cover the tables migrations create, so this runs after them.
            # --no-deps keeps it from building the API and worker images here.
            return subprocess.run(
                ["docker", "compose", "-f", "compose.generated.yaml", "--profile", "app",
                 "run", "--rm", "--no-deps", "roles"],
                cwd=root, env=environment, check=False,
            ).returncode
        profiles = ["--profile", "app"]
        if environment.get("KIP_EXTRA_PROFILE") == "semantic" and "models" in compose["services"]:
            # app-up.sh selected the compose model runtime for this machine.
            profiles += ["--profile", "semantic"]
        return subprocess.run(
            ["docker", "compose", "-f", "compose.generated.yaml", *profiles, *sys.argv[1:]],
            cwd=root, env=environment, check=False,
        ).returncode
    except (ConfigurationError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        # Resolver exceptions never include secret contents.
        print(f"setup runtime unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
