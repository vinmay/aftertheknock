"""The map's causal order (web/causal.js): run under node over every recorded run's map data."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from knock.game import Game
from knock.models import ReplayModel

ROOT = Path(__file__).resolve().parent.parent
RECORDS = sorted((ROOT / "data" / "runs" / "replays").glob("*.json")) + sorted((ROOT / "eval" / "results" / "runs").glob("*.json"))

CHECK = r"""
const { causalOrder } = require(process.argv[1]);
const runs = JSON.parse(require("fs").readFileSync(0, "utf8"));
const bad = [];
for (const [name, d] of runs) {
  const { col, order } = causalOrder(d);
  const at = Object.fromEntries(order.map((e, i) => [e.event_id, i]));
  const br = Object.fromEntries(d.branches.map((b) => [b.id, b]));
  if (order.length !== d.events.length) bad.push([name, "lost events"]);
  for (const x of d.intrusions) {
    if (!(at[x.origin_event_id] < at[x.manifestation_event_id])) bad.push([name, "manifestation before origin", x.intrusion_id]);
    if (!(col[x.origin_event_id] < col[x.manifestation_event_id])) bad.push([name, "same or earlier column", x.intrusion_id]);
  }
  for (const e of d.events) {
    for (const f of d.events) {
      if (e.branch === f.branch && e.seq < f.seq && !(at[e.event_id] < at[f.event_id])) bad.push([name, "branch order", e.event_id, f.event_id]);
    }
    const b = br[e.branch];
    if (b.parent) {
      for (const p of d.events.filter((p) => p.branch === b.parent && p.seq < b.fork_seq)) {
        if (!(col[p.event_id] < col[e.event_id])) bad.push([name, "child before parent", p.event_id, e.event_id]);
      }
    }
  }
  if (JSON.stringify(causalOrder(d).order.map((e) => e.event_id)) !== JSON.stringify(order.map((e) => e.event_id))) bad.push([name, "unstable"]);
}
console.log(JSON.stringify({ runs: runs.length, bad }));
"""


def map_data(p):
    rec = json.loads(p.read_text())
    g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False, run_id=rec["run_id"])
    for c in rec["choices"]:
        g.choose(c["index"])
    return [p.name, g.map_data()]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_origin_precedes_manifestation_in_every_recorded_run():
    assert RECORDS
    runs = [map_data(p) for p in RECORDS]
    r = subprocess.run(["node", "-e", CHECK, str(ROOT / "web" / "causal.js")], input=json.dumps(runs),
                       capture_output=True, text=True, check=True)
    out = json.loads(r.stdout)
    assert out["runs"] == len(RECORDS) and out["bad"] == [], out["bad"][:5]
