"""Sound keeps a desired state apart from its output (web/sound.js), run under node."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_sound_desired_state():
    r = subprocess.run(["node", str(ROOT / "tests" / "js" / "sound_state.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok ") == 6
