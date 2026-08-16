from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from kip.adapters.parsers.process_supervisor import (
    BoundedProcessResult,
    ParserIsolationLimits,
    run_bounded_process,
)
from kip.errors import ParserError


def _write_program(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_bounded_process_returns_file_response_without_pipe_capture(
    tmp_path: Path,
) -> None:
    # Given a child that writes its protocol response to the supplied file
    program = tmp_path / "write_response.py"
    response = tmp_path / "response.json"
    diagnostics = tmp_path / "diagnostics.log"
    _write_program(
        program,
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_bytes(b'{\\\"ok\\\":true}')\n",
    )

    # When it runs through the bounded process supervisor
    result = run_bounded_process(
        (sys.executable, str(program), str(response)),
        response_path=response,
        diagnostic_path=diagnostics,
        cwd=tmp_path,
        limits=ParserIsolationLimits.for_test(),
    )

    # Then only the bounded response artifact is returned
    assert result == BoundedProcessResult(
        returncode=0,
        response=b'{"ok":true}',
        diagnostic="",
    )


def test_bounded_process_kills_process_group_after_wall_timeout(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("process-group assertion requires POSIX")
    # Given a child that starts a grandchild and then hangs
    program = tmp_path / "hang_with_child.py"
    response = tmp_path / "response.json"
    diagnostics = tmp_path / "diagnostics.log"
    grandchild_pid = tmp_path / "grandchild.pid"
    _write_program(
        program,
        "from pathlib import Path\n"
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
    )

    # When the wall-clock deadline expires
    with pytest.raises(ParserError, match=r"timed out after 0\.2 seconds"):
        run_bounded_process(
            (sys.executable, str(program), str(grandchild_pid)),
            response_path=response,
            diagnostic_path=diagnostics,
            cwd=tmp_path,
            limits=ParserIsolationLimits.for_test(wall_seconds=0.2),
        )

    # Then the entire child process group is gone
    pid = int(grandchild_pid.read_text(encoding="utf-8"))
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("grandchild survived parser timeout")


def test_bounded_process_rejects_response_above_byte_limit(tmp_path: Path) -> None:
    # Given a child that emits a response larger than its contract budget
    program = tmp_path / "write_large_response.py"
    response = tmp_path / "response.json"
    diagnostics = tmp_path / "diagnostics.log"
    _write_program(
        program,
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_bytes(b'x' * 2048)\n",
    )

    # When the parent validates the response size
    with pytest.raises(ParserError, match="response exceeded 1024 bytes"):
        run_bounded_process(
            (sys.executable, str(program), str(response)),
            response_path=response,
            diagnostic_path=diagnostics,
            cwd=tmp_path,
            limits=ParserIsolationLimits.for_test(result_bytes=1024),
        )


def test_bounded_process_retains_only_diagnostic_tail(tmp_path: Path) -> None:
    program = tmp_path / "write_diagnostics.py"
    response = tmp_path / "response.json"
    diagnostics = tmp_path / "diagnostics.log"
    _write_program(
        program,
        "from pathlib import Path\n"
        "import sys\n"
        "sys.stderr.write('prefix-' + ('x' * 200) + '-tail-marker')\n"
        "Path(sys.argv[1]).write_bytes(b'{}')\n",
    )

    result = run_bounded_process(
        (sys.executable, str(program), str(response)),
        response_path=response,
        diagnostic_path=diagnostics,
        cwd=tmp_path,
        limits=replace(
            ParserIsolationLimits.for_test(),
            diagnostic_bytes=64,
        ),
    )

    assert len(result.diagnostic.encode("utf-8")) <= 64
    assert result.diagnostic.endswith("tail-marker")


def test_child_applies_cpu_file_and_descriptor_limits(tmp_path: Path) -> None:
    # Given a child process configured with explicit POSIX limits
    if os.name != "posix":
        pytest.skip("resource limits require POSIX")
    program = tmp_path / "report_limits.py"
    response = tmp_path / "response.txt"
    diagnostics = tmp_path / "diagnostics.log"
    _write_program(
        program,
        "from pathlib import Path\n"
        "import resource, sys\n"
        "from kip.adapters.parsers.process_supervisor import "
        "ParserIsolationLimits, apply_process_limits\n"
        "limits = ParserIsolationLimits.for_test()\n"
        "apply_process_limits(limits)\n"
        "cpu = resource.getrlimit(resource.RLIMIT_CPU)[0]\n"
        "file_size = resource.getrlimit(resource.RLIMIT_FSIZE)[0]\n"
        "descriptors = resource.getrlimit(resource.RLIMIT_NOFILE)[0]\n"
        "Path(sys.argv[1]).write_text(f'{cpu},{file_size},{descriptors}', encoding='utf-8')\n",
    )

    # When the child applies the profile
    result = run_bounded_process(
        (sys.executable, str(program), str(response)),
        response_path=response,
        diagnostic_path=diagnostics,
        cwd=Path.cwd(),
        limits=ParserIsolationLimits.for_test(),
    )

    # Then the kernel-visible soft limits match the configured values
    assert result.response == b"2,65536,256"


def test_bounded_process_kills_group_above_memory_budget(tmp_path: Path) -> None:
    # Given a child whose resident memory grows beyond the configured group budget
    program = tmp_path / "allocate_memory.py"
    response = tmp_path / "response.txt"
    diagnostics = tmp_path / "diagnostics.log"
    _write_program(
        program,
        "import time\n"
        "payload = bytearray(256 * 1024 * 1024)\n"
        "time.sleep(30)\n",
    )

    # When the supervisor observes the process-group RSS limit
    with pytest.raises(ParserError, match="memory budget of 100663296 bytes"):
        run_bounded_process(
            (sys.executable, str(program)),
            response_path=response,
            diagnostic_path=diagnostics,
            cwd=tmp_path,
            limits=ParserIsolationLimits.for_test(memory_bytes=96 * 1024 * 1024),
        )

    # Then no successful response is accepted
    assert not response.exists()
