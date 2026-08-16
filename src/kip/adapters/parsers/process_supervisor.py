from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psutil

from kip.errors import ConfigurationError, ParserError


@dataclass(frozen=True, slots=True)
class ParserIsolationLimits:
    wall_seconds: float = 180.0
    cpu_seconds: int = 120
    memory_bytes: int = 6 * 1024 * 1024 * 1024
    result_bytes: int = 256 * 1024 * 1024
    diagnostic_bytes: int = 16 * 1024
    cpu_threads: int = 4
    nice: int = 5

    def __post_init__(self) -> None:
        values = (
            self.wall_seconds,
            self.cpu_seconds,
            self.memory_bytes,
            self.result_bytes,
            self.diagnostic_bytes,
            self.cpu_threads,
        )
        if any(value <= 0 for value in values):
            raise ConfigurationError("parser isolation limits must be positive")
        if self.nice < 0 or self.nice > 19:
            raise ConfigurationError("parser isolation nice must be between 0 and 19")

    @classmethod
    def for_test(
        cls,
        *,
        wall_seconds: float = 5.0,
        memory_bytes: int = 512 * 1024 * 1024,
        result_bytes: int = 64 * 1024,
    ) -> ParserIsolationLimits:
        return cls(
            wall_seconds=wall_seconds,
            cpu_seconds=2,
            memory_bytes=memory_bytes,
            result_bytes=result_bytes,
            diagnostic_bytes=4096,
            cpu_threads=1,
            nice=0,
        )


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int
    response: bytes
    diagnostic: str


def run_bounded_process(
    argv: tuple[str, ...],
    *,
    response_path: Path,
    diagnostic_path: Path,
    cwd: Path,
    limits: ParserIsolationLimits,
    environment: Mapping[str, str] | None = None,
) -> BoundedProcessResult:
    try:
        with diagnostic_path.open("wb") as diagnostic_handle:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostic_handle,
                cwd=cwd,
                env=environment,
                start_new_session=os.name == "posix",
            )
            returncode = _wait_for_process(process, limits)
    except OSError as error:
        raise ParserError("parser process could not be started") from error

    response_size = response_path.stat().st_size if response_path.exists() else 0
    if response_size > limits.result_bytes:
        raise ParserError(
            f"parser process response exceeded {limits.result_bytes} bytes"
        )
    response = response_path.read_bytes() if response_path.exists() else b""
    diagnostic = _read_diagnostic(diagnostic_path, limits.diagnostic_bytes)
    return BoundedProcessResult(
        returncode=returncode,
        response=response,
        diagnostic=diagnostic,
    )


def apply_process_limits(limits: ParserIsolationLimits) -> None:
    if os.name != "posix":
        return
    import resource

    cpu_hard = limits.cpu_seconds + 5
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, cpu_hard))
    if sys.platform != "darwin":
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.memory_bytes, limits.memory_bytes),
        )
        if hasattr(resource, "RLIMIT_DATA"):
            resource.setrlimit(
                resource.RLIMIT_DATA,
                (limits.memory_bytes, limits.memory_bytes),
            )
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (limits.result_bytes, limits.result_bytes),
    )
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if limits.nice:
        os.nice(limits.nice)


def _wait_for_process(
    process: subprocess.Popen[bytes],
    limits: ParserIsolationLimits,
) -> int:
    deadline = time.monotonic() + limits.wall_seconds
    while True:
        returncode = process.poll()
        if returncode is not None:
            return returncode
        resident_bytes = _process_tree_resident_bytes(process.pid)
        if resident_bytes > limits.memory_bytes:
            _kill_process_tree(process)
            raise ParserError(
                f"parser process exceeded memory budget of {limits.memory_bytes} bytes"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process_tree(process)
            raise ParserError(
                f"parser process timed out after {limits.wall_seconds:g} seconds"
            )
        time.sleep(min(0.1, remaining))


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        process.kill()
    process.wait()


def _process_tree_resident_bytes(pid: int) -> int:
    try:
        process = psutil.Process(pid)
        descendants = process.children(recursive=True)
        resident_bytes = process.memory_info().rss
        for child in descendants:
            try:
                if child.is_running():
                    resident_bytes += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return resident_bytes
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return 0
    except psutil.AccessDenied as error:
        raise ParserError("parser process memory could not be inspected") from error


def _read_diagnostic(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit:
            handle.seek(size - limit)
        payload = handle.read(limit)
    return " ".join(payload.decode("utf-8", errors="replace").replace("\x00", " ").split())
