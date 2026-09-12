# ADR-061: Prepare prerequisites before loading the application environment

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Bootstrap previously loaded dotenv and generated credentials with Python before
checking whether Python existed. Recipients also had to arrange Node and
Docker themselves. A CLI binary alone did not establish engine readiness.

## Decision

`bootstrap.sh` starts with a Bash prerequisite stage that does not import KIP
or load dotenv. It locates compatible Python or downloads checksum-pinned uv,
which installs managed Python into the project without changing global Python
links. A standard-library Python stage then validates dotenv, prepares the
pinned uv/Node runtime when needed, and checks Docker/Compose and engine access.
`requirements/bootstrap.tsv` supplies platform URLs, digests and versions.
The project `.venv` is created with uv, without requiring system ensurepip.

Compatible existing runtimes and environments are reused. Managed executable
paths are shared by wrappers; shell profiles, system Python/Node, Docker
contexts and group membership are not rewritten. Downloads are verified before
execution and staged under the project; incomplete directories are preserved
for diagnosis rather than deleted. `--check` is read-only.

System Docker installation requires explicit authorization (an interactive
confirmation or `--install-docker`). macOS uses a pinned vendor DMG and native
installer; Ubuntu/Debian use Docker's signed repository. Existing conflicting
container packages are not removed. Desktop license/first-run decisions,
administrator authentication and Linux engine access remain user-controlled.
WSL2 users configure Windows Docker Desktop and WSL integration. Engine checks
are bounded and an incomplete setup exits with an action-required status.

`--without-docker` supports externally supplied databases and runtime-only CI.
Guided external-database readiness therefore does not require Docker for the
host CLI/MCP path; the optional API/worker container profile still does.

## Validation and limits

Tests cover checksum rejection, safe extraction, repeat runs, path containment,
no-Python checks, frozen dependency installation and Docker action boundaries.
CI executes a real download/bootstrap with host Python/Node/uv hidden from PATH.
Native privileged Docker installation is not claimed to be verified on every
supported OS by those simulated tests. Minimal Linux still needs a HTTPS
transfer tool, CA certificates, tar/gzip and a SHA-256 utility for the first download.
Managed runtimes are deployment-owned state and are never shipped in source ZIPs.
