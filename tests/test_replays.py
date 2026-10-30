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


def test_other_heard_in_recorded_runs_is_the_models_own_line():
    """In real runs the climax line, when present, is a committed model speech by Mara in shadow B."""
    runs = REPLAYS + sorted((ROOT / "eval" / "results" / "hosted" / "runs").glob("*.json"))
    fired = 0
    for p in runs:
        rec = json.loads(p.read_text())
        g = replay(rec)
        e = g.mara_heard()
        slot = g.other_you()["slots"]["other_heard"]
        if e is None:
            assert slot == "", p.name
            continue
        fired += 1
        logged = [r for r in g.log.records if r.get("kind") == "proposal" and r.get("event_id") == e.event_id]
        assert e.source == "model" and logged and logged[0]["decision"]["accepted"], p.name
        assert e.text.strip().rstrip(".?!") in slot, p.name
    assert fired


def test_other_detail_matches_how_the_object_changed_hands():
    """'Out of my hand' only after Mara's own take; 'I gave her' only after the other you's give."""
    runs = REPLAYS + sorted((ROOT / "eval" / "results" / "hosted" / "runs").glob("*.json")) + sorted((ROOT / "eval" / "results" / "runs").glob("*.json"))
    for p in runs:
        g = replay(json.loads(p.read_text()))
        other = g.other_you()
        detail, b = other["slots"]["other_detail"], {e.event_id: e for e in g.timeline.branch("B").events}
        hands = [b[i] for i in other["claims"] if b[i].target == "pocket" and b[i].action in ("take", "give")]
        if "out of my hand" in detail:
            assert hands and hands[0].action == "take" and hands[0].actor == "mara", p.name
        if "I gave her" in detail:
            assert hands and hands[0].action == "give" and hands[0].actor == "player", p.name
