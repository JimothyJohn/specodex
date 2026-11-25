"""scripts/build_backend_py.sh: the Docker bundling path must hand pip the
pinned requirement specs intact.

Regression (2026-11-25): the payload was ``bash -c "pip install ... ${DEPS[*]}"``,
so the inner shell parsed ``fastapi>=0.115.0`` as ``fastapi`` plus a redirect
into a file named ``=0.115.0`` in the repo root — every lower bound in the
v2 Lambda bundle was silently dropped, and each ``./Quickstart verify`` left
``=0.115.0`` … ``=3.3.0`` litter (pip's log) at the repo root.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build_backend_py.sh"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _declared_deps() -> list[str]:
    block = re.search(r"DEPS=\((.*?)\)", SCRIPT.read_text(), re.S)
    assert block, "DEPS array not found"
    return re.findall(r'"([^"]+)"', block.group(1))


@pytest.mark.unit
def test_docker_payload_passes_pinned_specs_to_pip(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / SCRIPT.name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload_file = tmp_path / "payload.sh"
    pip_args = tmp_path / "pip_args.json"
    # docker: `info` succeeds; `run` saves the `bash -c` payload and stops.
    _exe(
        bin_dir / "docker",
        "#!/usr/bin/env bash\n"
        'if [ "$1" = info ]; then exit 0; fi\n'
        f'printf "%s" "${{@: -1}}" > "{payload_file}"\n'
        "exit 1\n",
    )
    _exe(
        bin_dir / "pip",
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(pip_args)!r}, 'w'))\n"
        "sys.exit(1)\n",
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name)],
        env=env,
        capture_output=True,
        timeout=60,
    )
    payload = payload_file.read_text()
    assert "pip install" in payload, payload

    # Run the captured payload the way the container would (pip stubbed,
    # so the && chain stops after recording argv).
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(
        ["bash", "-c", payload], cwd=work, env=env, capture_output=True, timeout=60
    )
    args = json.loads(pip_args.read_text())
    assert args[:3] == ["install", "--target", "/asset-output"], args
    assert args[3:] == _declared_deps(), args
    assert not list(work.glob("=*")), sorted(p.name for p in work.iterdir())
