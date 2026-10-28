"""The crossing stage is ready when its last line has revealed itself (web/reveal.js), run under node."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_reveal_gate():
    r = subprocess.run(["node", str(ROOT / "tests" / "js" / "reveal.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok ") == 5
