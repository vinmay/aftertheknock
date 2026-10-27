"""Committed replays must still reproduce with the current engine, prompts and story."""

import json
from pathlib import Path

import pytest

from knock.game import replay, verify_replay

REPLAYS = sorted((Path(__file__).resolve().parent.parent / "data" / "runs" / "replays").glob("*.json"))


@pytest.mark.parametrize("path", REPLAYS, ids=[p.stem for p in REPLAYS])
def test_committed_replay_verifies(path):
    rec = json.loads(path.read_text())
    g = replay(rec)
    assert verify_replay(g, rec), f"{path.name}: re-record it with scripts/record_replays"
    assert g.ending == rec["ending"]
