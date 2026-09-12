from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("existing_uv", [True, False])
def test_bootstrap_installs_declared_extras_from_frozen_lock(
    tmp_path: Path, existing_uv: bool,
) -> None:
    project = tmp_path / "project space"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/bootstrap.sh", scripts / "bootstrap.sh")
    (scripts / "common.sh").write_text(
        'PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
        'export PROJECT_ROOT\n'
        'export PATH="$PROJECT_ROOT/var/runtime/bin:$PATH"\n'
        'python_cmd() { printf "%s\\n" "$PROJECT_ROOT/.venv/bin/python"; }\n'
    )
    (scripts / "install-kordoc.sh").write_text("#!/bin/bash\nexit 0\n")
    (scripts / "install-kordoc.sh").chmod(0o755)
    (project / "config").mkdir()
    (project / "config/kip.toml").write_text("existing configuration\n")
    (project / ".env").write_text("existing private environment\n")
    commands = project / "commands.jsonl"
    record = (
        "import json,os,sys\nfrom pathlib import Path\n"
        "with Path(os.environ['BOOTSTRAP_COMMANDS']).open('a') as log:\n"
        "    log.write(json.dumps({'program':PROGRAM,'args':sys.argv[1:]})+'\\n')\n"
    )
    uv_stub = tmp_path / "uv-stub"
    uv_stub.write_text(f"#!{sys.executable}\nPROGRAM='uv'\n" + record)
    uv_stub.chmod(0o755)
    (scripts / "prerequisites.sh").write_text(
        '#!/bin/bash\nset -eu\n'
        'root="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'if ! command -v uv >/dev/null; then\n'
        '  mkdir -p "$root/var/runtime/bin"\n'
        '  cp "$BOOTSTRAP_UV_STUB" "$root/var/runtime/bin/uv"\n'
        'fi\n'
    )
    (scripts / "prerequisites.sh").chmod(0o755)
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!{sys.executable}\nPROGRAM='python'\n" + record
    )
    python.chmod(0o755)
    (python.parent / "activate").write_text('export PATH="$PROJECT_ROOT/.venv/bin:$PATH"\n')
    binary = tmp_path / "bin"
    binary.mkdir()
    if existing_uv:
        shutil.copy2(uv_stub, binary / "uv")
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("KIP_")},
        "PATH": f"{binary}:/usr/bin:/bin",
        "BOOTSTRAP_COMMANDS": str(commands),
        "BOOTSTRAP_UV_STUB": str(uv_stub),
    }
    for _ in range(2):
        subprocess.run(
            ["bash", str(scripts / "bootstrap.sh")], cwd=project,
            env=environment, check=True, capture_output=True, text=True,
        )
    calls = [json.loads(line) for line in commands.read_text().splitlines()]
    syncs = [call["args"] for call in calls if call["program"] == "uv"]
    assert len(syncs) == 2
    for arguments in syncs:
        assert arguments[:2] == ["sync", "--frozen"]
        assert arguments[arguments.index("--python") + 1] == str(python)
        assert {
            arguments[index + 1] for index, argument in enumerate(arguments)
            if argument == "--extra"
        } == {"postgres", "api", "identity", "extractors", "mcp", "dev"}
    installs = [call["args"] for call in calls if call["args"][:3] == ["-m", "pip", "install"]]
    assert installs == []
    if not existing_uv:
        tool = project / "var/runtime/bin/uv"
        assert tool.is_file()
        assert not tool.is_relative_to(project / ".venv")
    assert (project / ".env").read_text() == "existing private environment\n"
    assert (project / "config/kip.toml").read_text() == "existing configuration\n"
