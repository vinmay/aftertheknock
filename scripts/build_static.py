"""Build the static replay site into site/ (committed; Render publishes it as is).

    uv run python scripts/build_static.py          # build
    uv run python scripts/build_static.py --check  # exit 1 if site/ is out of date

Each committed replay is re-executed through the engine first. The build refuses to
publish a replay whose state hashes do not match its recording. Then every step a
player can see (scene view, trace, map, evidence log so far) is exported as JSON, so
the site needs no backend and no model.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knock.game import Game, replay_card, verify_replay  # noqa: E402
from knock.models import ReplayModel  # noqa: E402

WEB = ROOT / "web"
REPLAYS = ROOT / "data" / "runs" / "replays"
SITE = ROOT / "site"
STATIC_FLAG = '<script>window.KNOCK_STATIC = true;</script>\n<script src="app.js"></script>'


def export(rec: dict) -> dict:
    g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False, run_id=rec["run_id"])
    steps = [{"view": g.view(), "log_len": len(g.log.records)}]
    for ch in rec["choices"]:
        v = g.choose(ch["index"])
        steps.append({"view": v, "log_len": len(g.log.records)})
    if not verify_replay(g, rec):
        raise SystemExit(f"replay {rec['run_id']} does not reproduce its recorded state; re-record it")
    steps[-1]["view"]["verified"] = True
    # Show the recorded log (real latencies and raw model text); the replay's log has the
    # same records in the same order, but timed against the instant replay model.
    assert [r["kind"] for r in g.log.records] == [r["kind"] for r in rec["log"]]
    g.log.records = rec["log"]
    return {"id": None, "run_id": rec["run_id"], "seed": rec["seed"], "pocket": g.pocket,
            "model": rec["model"], "choices": [c["index"] for c in rec["choices"]],
            "path": f"{rec['fork1']} / {rec['fork2']} / {rec['ending']['which']}",
            "state_hashes": rec["state_hashes"], "steps": steps, "map": g.map_data(),
            "log": rec["log"], "ops": g.timeline.ops}


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "replays").mkdir(parents=True)
    for name in ("app.js", "causal.js", "sound.js", "style.css"):
        shutil.copy(WEB / name, out / name)
    html = (WEB / "index.html").read_text()
    assert '<script src="app.js"></script>' in html
    (out / "index.html").write_text(html.replace('<script src="app.js"></script>', STATIC_FLAG))
    index = []
    for p in sorted(REPLAYS.glob("*.json")):
        data = export(json.loads(p.read_text()))
        data["id"] = p.stem
        (out / "replays" / f"{p.stem}.json").write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))
        index.append(replay_card(p.stem, json.loads(p.read_text())))
    index.sort(key=lambda c: not c["recommended"])
    (out / "replays" / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))


def same_tree(a: Path, b: Path) -> bool:
    fa = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    fb = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    return fa == fb and all((a / f).read_bytes() == (b / f).read_bytes() for f in fa)


if __name__ == "__main__":
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "site")
            ok = SITE.exists() and same_tree(Path(tmp) / "site", SITE)
        print("site/ is up to date" if ok else "site/ is out of date: run scripts/build_static.py")
        sys.exit(0 if ok else 1)
    build(SITE)
    print(f"built {SITE}")
