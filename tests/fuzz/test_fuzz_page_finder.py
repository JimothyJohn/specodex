"""Short bounded atheris run, selected with ``pytest -m fuzz``.

Skips unless the opt-in ``fuzz`` dependency group is installed
(``uv run --group fuzz pytest tests/fuzz -m fuzz``). Long sessions go
through the harness directly — see ``fuzz_page_finder.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("atheris")

HARNESS = Path(__file__).parent / "fuzz_page_finder.py"


@pytest.mark.fuzz
def test_page_finder_fuzz_smoke(tmp_path) -> None:
    for seed in (Path(__file__).parent / "corpus").iterdir():
        (tmp_path / seed.name).write_bytes(seed.read_bytes())
    proc = subprocess.run(
        [sys.executable, str(HARNESS), str(tmp_path), "-runs=300", "-timeout=30"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
