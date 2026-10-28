"""Committed replays must still reproduce with the current engine, prompts and story."""

import json
from pathlib import Path

import pytest

from knock.game import replay, verify_replay

ROOT = Path(__file__).resolve().parent.parent
REPLAYS = sorted((ROOT / "data" / "runs" / "replays").glob("*.json"))


@pytest.mark.parametrize("path", REPLAYS, ids=[p.stem for p in REPLAYS])
def test_committed_replay_verifies(path):
    rec = json.loads(path.read_text())
    g = replay(rec)
    assert verify_replay(g, rec), f"{path.name}: re-record it with scripts/record_replays"
    assert g.ending == rec["ending"]


def test_static_site_is_up_to_date():
    """site/ is what Render publishes; it must match the current replays and front end."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "build_static.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_nothing_is_left_invisible_when_animations_are_off():
    """Elements that fade in from opacity 0 must be forced visible when fades are disabled
    (instant text or reduced motion); otherwise choices exist but can't be seen."""
    import re
    css = (Path(__file__).resolve().parent.parent / "web" / "style.css").read_text()
    for sel in (".choices", ".text p", ".stage-after", ".stage-color", ".trace-hint"):
        assert re.search(r"body\.instant-text[^{]*" + re.escape(sel) + r"[^{]*\{[^}]*opacity:\s*1", css), sel


def test_crossing_two_is_written_by_the_player_in_every_recorded_run():
    """Shadow D's mark (crossing 2) is the other you's message, in the player's own hand."""
    from knock.game import Game
    from knock.models import ReplayModel
    runs = sorted((ROOT / "data" / "runs" / "replays").glob("*.json")) + sorted((ROOT / "eval" / "results" / "runs").glob("*.json"))
    assert runs
    for p in runs:
        rec = json.loads(p.read_text())
        g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False, run_id=rec["run_id"])
        for c in rec["choices"]:
            g.choose(c["index"])
        d = g.timeline.event(g.crossings["D"]["intrusion"]["origin_event_id"])
        assert d.actor == "player", (p.name, d.actor)
