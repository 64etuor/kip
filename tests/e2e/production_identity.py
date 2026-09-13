#!/usr/bin/env python3
"""Construct the identity each service of the rendered production Compose file builds.

3.13.0 shipped `compose.production.yaml` with a `KIP_IDENTITY_MODE` that
`src/kip/container.py` rejects, so its API aborted at start-up, while every
check green at the time booted `compose.yaml` instead. Booting the production
file needs digest-pinned images, secret files, a JWKS issuer and a NAS; this
check needs none of them.

`scripts/e2e-db-roles.sh --mode production-config` renders the file with
`docker compose config --format json`. This module takes each named service's
resolved `environment`, maps the container paths it names onto this checkout
(`/app/...` is where the runtime image copies it, `/run/secrets/...` become
generated placeholder files), and runs the same `Settings.load()` and identity
construction the container runs before it does anything. That is every
KIP process, not only the API: `kip.worker.main` and every `kip` CLI command
except setup/update/version (so `migrate`) build the container first.

It also proves it is not vacuous: the control service's environment with the
identity mode 3.13.0 shipped (`jwt`) must be rejected. Every service and the
control are evaluated and every failure is reported, not only the first.

Not covered: anything after identity construction - the database, the real
secrets, the JWKS endpoint, `/readyz`. Nothing here starts a container.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from kip.container import _build_identity
from kip.settings import Settings

# The runtime image's WORKDIR (Dockerfile): `COPY config ./config` puts this
# checkout's config/ directory at /app/config.
IMAGE_ROOT = PurePosixPath("/app")
# The value 3.13.0's compose.production.yaml carried.
REJECTED_MODE = "jwt"
# Settings.load() needs a non-empty database URL; nothing here connects.
PLACEHOLDER_DATABASE_URL = "postgresql://postgres/kip"


class CheckFailed(Exception):
    """The rendered file cannot be evaluated for one service."""


def _service_environment(rendered: dict[str, Any], service: str) -> dict[str, str]:
    services = rendered.get("services") or {}
    if service not in services:
        raise CheckFailed(f"the rendered compose file has no service {service!r}")
    environment = services[service].get("environment") or {}
    if isinstance(environment, list):
        environment = dict(str(item).partition("=")[::2] for item in environment)
    return {
        str(key): "" if value is None else str(value)
        for key, value in environment.items()
    }


def _host_environment(
    container: dict[str, str], project_root: Path, scratch: Path
) -> dict[str, str]:
    host = {"KIP_PROJECT_ROOT": str(project_root)}
    for key, value in container.items():
        path = PurePosixPath(value)
        if key.endswith("_FILE") and value.startswith("/run/secrets/"):
            secret = scratch / path.name
            secret.write_text(PLACEHOLDER_DATABASE_URL + "\n", encoding="utf-8")
            secret.chmod(0o600)
            host[key] = str(secret)
        elif key == "KIP_CAS_PATH":
            host[key] = str(scratch / "cas")
        elif path.is_absolute() and path.is_relative_to(IMAGE_ROOT):
            mapped = project_root / path.relative_to(IMAGE_ROOT)
            if not mapped.exists():
                raise CheckFailed(
                    f"{key}={value} maps to {mapped}, which this checkout does not have"
                )
            host[key] = str(mapped)
        else:
            host[key] = value
    return host


def _construct(environment: dict[str, str]) -> tuple[str, str]:
    # Only the rendered environment may configure KIP; nothing ambient.
    for key in [key for key in os.environ if key.startswith("KIP_")]:
        del os.environ[key]
    os.environ.update(environment)
    settings = Settings.load()
    identity = _build_identity(settings)
    return settings.identity_mode, type(identity).__name__


def _evaluate(
    rendered: dict[str, Any], args: argparse.Namespace, root: Path, scratch: Path
) -> list[str]:
    failures: list[str] = []
    for service in args.service:
        try:
            container = _service_environment(rendered, service)
            declared = container.get("KIP_IDENTITY_MODE", "(unset; from KIP_CONFIG)")
            host = _host_environment(container, root, scratch)
        except CheckFailed as failure:
            failures.append(f"{service}: {failure}")
            continue
        try:
            mode, adapter = _construct(host)
        except Exception as error:
            failures.append(
                f"{service}: KIP_IDENTITY_MODE={declared} would abort start-up: "
                f"{type(error).__name__}: {error}"
            )
            continue
        print(f"{service}: KIP_IDENTITY_MODE={declared} -> mode {mode} -> {adapter}")

    label = f"control: {args.control_service} with KIP_IDENTITY_MODE={REJECTED_MODE}"
    try:
        control = _host_environment(
            _service_environment(rendered, args.control_service), root, scratch
        )
    except CheckFailed as failure:
        return [*failures, f"{label}: {failure}"]
    control["KIP_IDENTITY_MODE"] = REJECTED_MODE
    try:
        _construct(control)
    except Exception as error:
        if "unsupported identity mode" in str(error):
            print(f"{label} is rejected: {error}")
        else:
            failures.append(f"{label} failed for another reason, so it proves nothing: {error}")
    else:
        failures.append(f"{label} was accepted, so this check cannot detect the 3.13.0 defect")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("rendered", type=Path, help="`docker compose config --format json` output")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--service", action="append", required=True)
    parser.add_argument("--control-service", default="api")
    args = parser.parse_args(argv)
    rendered = json.loads(args.rendered.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="kip-e2e-identity.") as scratch_name:
        failures = _evaluate(rendered, args, args.project_root.resolve(), Path(scratch_name))
    for failure in failures:
        print(f"production_identity: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
