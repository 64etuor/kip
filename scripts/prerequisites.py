#!/usr/bin/env python3
"""Standard-library-only prerequisite preparation; safe before pip installs."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from load_dotenv import parse_dotenv

ROOT = Path(__file__).resolve().parents[1]


class ActionRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    component: str
    target: str
    version: str
    url: str
    sha256: str


def assets(root: Path) -> dict[tuple[str, str], Asset]:
    result: dict[tuple[str, str], Asset] = {}
    for line in (root / "requirements/bootstrap.tsv").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        asset = Asset(*line.split())
        if asset.component not in {"uv", "python", "node", "docker", "docker-key"}:
            raise ValueError("unsupported bootstrap component")
        if asset.component != "docker-key" and not re.fullmatch(r"\d+\.\d+\.\d+", asset.version):
            raise ValueError("bootstrap versions must be exact numeric versions")
        if asset.component != "python" and (not asset.url.startswith("https://") or not re.fullmatch(r"[a-f0-9]{64}", asset.sha256)):
            raise ValueError("bootstrap assets require an HTTPS URL and SHA-256 pin")
        key = (asset.component, asset.target)
        if key in result:
            raise ValueError(f"duplicate bootstrap asset: {key}")
        result[key] = asset
    return result


def run(args: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 1, "", "command unavailable or timed out")


def version_ok(text: str, minimum: tuple[int, ...]) -> bool:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return bool(match and tuple(int(value or 0) for value in match.groups()) >= minimum)


def asset_version_ok(asset: Asset, text: str) -> bool:
    if asset.component == "uv":
        return text.split()[:2] == ["uv", asset.version]
    return text.strip() == "v" + asset.version


def target_platform() -> str:
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine)
    if sys.platform not in {"darwin", "linux"} or arch is None:
        raise ActionRequired("Use macOS or glibc Linux on arm64/x86_64. On Windows, run bootstrap inside WSL2.")
    if sys.platform == "linux" and (platform.libc_ver()[0] == "musl" or Path("/etc/alpine-release").exists()):
        raise ActionRequired("Automatic runtimes require glibc Linux; use Ubuntu/Debian rather than Alpine.")
    return f"{sys.platform}-{arch}"


def download(asset: Asset, destination: Path) -> None:
    if not asset.url.startswith("https://") or not re.fullmatch(r"[a-f0-9]{64}", asset.sha256):
        raise RuntimeError("invalid pinned bootstrap asset")
    digest = hashlib.sha256()
    started = time.monotonic()
    maximum = {"uv": 64, "node": 128, "docker": 2048, "docker-key": 1}[asset.component] * 1024 * 1024
    received = 0
    with urllib.request.urlopen(asset.url, timeout=30) as response, destination.open("wb") as output:
        if not response.url.startswith("https://"):
            raise RuntimeError("bootstrap download redirected away from HTTPS")
        while block := response.read(1024 * 1024):
            received += len(block)
            if received > maximum:
                raise RuntimeError(f"{asset.component} download exceeded its size limit")
            if time.monotonic() - started > 900:
                raise RuntimeError("bootstrap download exceeded 15 minutes; rerun to retry")
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != asset.sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"{asset.component} checksum mismatch; nothing from this download was executed")


def link_program(root: Path, name: str, program: Path) -> None:
    directory = root / "var/runtime/bin"
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise ActionRequired("Review the symlinked runtime/bin path before installing programs.")
    directory.mkdir(parents=True, exist_ok=True)
    link = directory / name
    if program.absolute() == link.absolute():
        return
    if link.exists() and not link.is_symlink():
        raise ActionRequired(f"Preserve the existing non-managed program {link} elsewhere before retrying.")
    temporary = directory / f".{name}-{os.getpid()}"
    temporary.symlink_to(program)
    os.replace(temporary, link)


def prepare_bundle(root: Path, asset: Asset) -> Path:
    runtime = root / "var/runtime"
    if not runtime.resolve().is_relative_to(root.resolve()):
        raise ActionRequired("var/runtime points outside this project; review that path before installing runtimes.")
    runtime.mkdir(parents=True, exist_ok=True)
    destination = runtime / f"{asset.component}-{asset.version}"
    marker = destination / ".verified-sha256"
    if destination.is_symlink():
        raise ActionRequired("Managed runtime directories must not be symlinks.")
    binary = destination / ("uv" if asset.component == "uv" else "bin/node")
    if marker.is_file() and marker.read_text().strip() == asset.sha256 and binary.is_file():
        probe = run([str(binary), "--version"])
        if probe.returncode == 0 and asset_version_ok(asset, probe.stdout):
            return destination
    if destination.exists():
        raise ActionRequired(f"Incomplete or changed runtime {destination}. Preserve it under another name and rerun.")
    print(f"Preparing {asset.component} {asset.version} in this project…", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=runtime) as staging:
        stage = Path(staging)
        archive = stage / "archive.tar.gz"
        download(asset, archive)
        payload = stage / "payload"
        payload.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(payload, filter="data")
        entries = list(payload.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise RuntimeError("runtime archive must contain one directory")
        candidate = entries[0]
        probe_binary = candidate / ("uv" if asset.component == "uv" else "bin/node")
        probe = run([str(probe_binary), "--version"])
        if probe.returncode or not asset_version_ok(asset, probe.stdout):
            raise RuntimeError(f"downloaded {asset.component} did not pass its version probe")
        (candidate / ".verified-sha256").write_text(asset.sha256 + "\n")
        candidate.rename(destination)
    return destination


def docker_ready() -> bool:
    compose = run(["docker", "compose", "version"])
    return compose.returncode == 0 and version_ok(compose.stdout, (2, 20, 0)) and run(["docker", "info"], timeout=5).returncode == 0


def privileged(args: list[str]) -> None:
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            raise ActionRequired("Docker installation needs an administrator and sudo. Ask your administrator to install Docker, then rerun bootstrap.")
        if not sys.stdin.isatty() and run(["sudo", "-n", "true"]).returncode:
            raise ActionRequired("Administrator authentication is required. Run ./scripts/bootstrap.sh --install-docker in an interactive terminal; do not send passwords to the agent.")
        args = ["sudo", *args]
    subprocess.run(args, check=True, stdout=sys.stderr)


def install_docker(root: Path, catalog: dict[tuple[str, str], Asset], target: str, *, compose_only: bool = False) -> None:
    print("Installing Docker changes system software and may request administrator authentication.", file=sys.stderr)
    if target.startswith("darwin-"):
        app = Path("/Applications/Docker.app")
        if app.exists():
            return  # Never replace an existing Desktop installation.
        if int(platform.mac_ver()[0].split(".")[0]) < 14:
            raise ActionRequired("This Docker Desktop installer requires macOS 14+. Upgrade macOS or use an externally managed database.")
        # Check administrator interaction before downloading a large image.
        privileged(["true"])
        docker_target = target
        if target == "darwin-x64" and run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"]).stdout.strip() == "1":
            docker_target = "darwin-arm64"  # Native Desktop even from a Rosetta Python.
        with tempfile.TemporaryDirectory(prefix="kip-docker-") as staging:
            directory = Path(staging)
            image = directory / "Docker.dmg"
            download(catalog[("docker", docker_target)], image)
            mount = directory / "mount"
            mount.mkdir()
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(image)], check=True, stdout=sys.stderr)
            try:
                # License acceptance and first-run choices stay with the user.
                privileged([str(mount / "Docker.app/Contents/MacOS/install")])
            finally:
                subprocess.run(["hdiutil", "detach", str(mount)], check=False, stdout=sys.stderr)
        return
    if "microsoft" in platform.release().lower():
        raise ActionRequired("WSL2: install Docker Desktop on Windows (winget install --exact --id Docker.DockerDesktop --interactive), enable integration for this WSL distribution in Docker Desktop, then rerun bootstrap.")
    os_release = platform.freedesktop_os_release()
    distribution = os_release.get("ID", "")
    codename = os_release.get("VERSION_CODENAME", "")
    if distribution not in {"ubuntu", "debian"} or not re.fullmatch(r"[a-z]+", codename):
        raise ActionRequired("Automatic Docker Engine installation supports Ubuntu/Debian. Install Engine and Compose using https://docs.docker.com/engine/install/ for this distribution, then rerun bootstrap.")
    conflicts = [name for name in ("docker.io", "podman-docker", "containerd", "runc")
                 if run(["dpkg-query", "-W", "-f=${Status}", name]).stdout.strip() == "install ok installed"]
    if conflicts:
        raise ActionRequired("Existing container packages need administrator review; nothing was removed: " + ", ".join(conflicts))
    repository = Path("/etc/apt/sources.list.d/docker.sources")
    if not repository.exists() and not Path("/etc/apt/sources.list.d/docker.list").exists():
        with tempfile.TemporaryDirectory(prefix="kip-docker-repo-") as staging:
            directory = Path(staging)
            key = directory / "docker.asc"
            download(catalog[("docker-key", distribution)], key)
            existing_key = Path("/etc/apt/keyrings/docker.asc")
            if existing_key.exists() and hashlib.sha256(existing_key.read_bytes()).hexdigest() != catalog[("docker-key", distribution)].sha256:
                raise ActionRequired("An existing Docker signing key differs from the expected key. Ask your administrator to review it; it was not overwritten.")
            arch = "arm64" if target.endswith("arm64") else "amd64"
            config = directory / "docker.sources"
            config.write_text(f"Types: deb\nURIs: https://download.docker.com/linux/{distribution}\nSuites: {codename}\nComponents: stable\nArchitectures: {arch}\nSigned-By: /etc/apt/keyrings/docker.asc\n")
            privileged(["install", "-m", "0755", "-d", "/etc/apt/keyrings"])
            privileged(["install", "-m", "0644", str(key), "/etc/apt/keyrings/docker.asc"])
            privileged(["install", "-m", "0644", str(config), str(repository)])
    privileged(["apt-get", "update"])
    packages = ["docker-compose-plugin"] if compose_only else ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]
    privileged(["apt-get", "install", "-y", *packages])
    if not compose_only:
        privileged(["systemctl", "enable", "--now", "docker"])


def ensure_docker(root: Path, catalog: dict[tuple[str, str], Asset], target: str, *, install: bool, check: bool) -> None:
    if docker_ready():
        print("Docker + Compose: ready")
        return
    if check:
        raise ActionRequired("Docker/Compose or engine access is not ready. Run bootstrap, or use --install-docker to authorize installation.")
    compose_only = bool(shutil.which("docker"))
    compose = run(["docker", "compose", "version"]) if compose_only else None
    if compose is not None and compose.returncode == 0 and not version_ok(compose.stdout, (2, 20, 0)):
        raise ActionRequired("Docker Compose 2.20+ is required. Update the existing Compose client/Desktop installation, then rerun bootstrap; the installer will not replace a running engine.")
    context = run(["docker", "context", "show"]) if compose_only else subprocess.CompletedProcess([], 0, "", "")
    local_context = context.stdout.strip() in {"", "default", "desktop-linux"}
    if compose_only and (not local_context or os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT")):
        raise ActionRequired("The selected Docker endpoint or its Compose client is not ready. Start/fix that engine and rerun bootstrap. The installer will not replace your Docker context or start another engine.")
    desktop_missing = target.startswith("darwin-") and not any(
        path.exists() for path in (Path("/Applications/Docker.app"), Path.home() / "Applications/Docker.app")
    )
    if not compose_only or (compose is not None and compose.returncode) or desktop_missing:
        if not install and sys.stdin.isatty():
            print("Docker engine/Compose is missing. Install Docker system software? [y/N] ", end="", file=sys.stderr, flush=True)
            install = input().strip().casefold() in {"y", "yes"}
        if not install:
            raise ActionRequired("Docker is missing. Run ./scripts/bootstrap.sh --install-docker to install it, or --without-docker when using an external database.")
        install_docker(root, catalog, target, compose_only=compose_only)
        docker_bin = Path("/Applications/Docker.app/Contents/Resources/bin")
        if (docker_bin / "docker").is_file():
            os.environ["PATH"] = str(docker_bin) + os.pathsep + os.environ["PATH"]
    if target.startswith("linux-") and install and local_context and not os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"):
        privileged(["systemctl", "start", "docker"])
    if target.startswith("darwin-") and local_context and not os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"):
        subprocess.run(["open", "-a", "Docker"], check=False, stdout=sys.stderr)
        print("Waiting up to 60 seconds for Docker. Complete first-run choices in its window if prompted.", file=sys.stderr)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if docker_ready():
                print("Docker + Compose: ready")
                return
            time.sleep(2)
    if docker_ready():
        print("Docker + Compose: ready")
        return
    raise ActionRequired("Docker is installed but the selected engine is not accessible. Complete Desktop setup or start your selected engine. On Linux, ask your administrator to grant Docker access (docker group access is root-equivalent; signing in again may be required), or configure rootless Docker. No context or group membership was changed. Rerun the same bootstrap command afterwards.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read-only readiness check")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install-docker", action="store_true", help="authorize system Docker installation if missing")
    group.add_argument("--without-docker", action="store_true", help="external-database/CI path: explicitly skip Docker readiness")
    args = parser.parse_args()
    try:
        target = target_platform()
        catalog = assets(ROOT)
        if (ROOT / ".env").is_file() and os.environ.get("KIP_SKIP_DOTENV") != "1":
            for key, value in parse_dotenv(ROOT / ".env"):
                os.environ.setdefault(key, value)
        print(f"Python: ready ({platform.python_version()})")
        if not args.check:
            uv = prepare_bundle(ROOT, catalog[("uv", target)])
            link_program(ROOT, "uv", uv / "uv")
            if not Path(sys.executable).is_relative_to(ROOT / ".venv"):
                program = Path(sys.executable)
                link_program(ROOT, "python3", program.resolve() if program.parent == ROOT / "var/runtime/bin" else program)
        node = run(["node", "--version"])
        npm = run(["npm", "--version"])
        if node.returncode or not version_ok(node.stdout, (20, 9, 0)) or npm.returncode or not version_ok(npm.stdout, (9, 0, 0)):
            if args.check:
                raise ActionRequired("Node.js 20.9+ / npm 9+ are missing or too old. Run ./scripts/bootstrap.sh to prepare them in this project.")
            package = prepare_bundle(ROOT, catalog[("node", target)])
            link_program(ROOT, "node", package / "bin/node")
            link_program(ROOT, "npm", package / "bin/npm")
            node = run(["node", "--version"])
        if node.returncode or run(["npm", "--version"]).returncode:
            raise RuntimeError("Node/npm readiness probe failed after installation")
        print(f"Node + npm: ready ({node.stdout.strip()})")
        if args.without_docker:
            print("Docker: explicitly skipped (--without-docker)")
        else:
            ensure_docker(ROOT, catalog, target, install=args.install_docker, check=args.check)
        return 0
    except ActionRequired as error:
        print(f"Action required: {error}", file=sys.stderr)
        return 75
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"Prerequisite preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
