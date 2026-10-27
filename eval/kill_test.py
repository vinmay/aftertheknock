"""Kill test (handoff section 9), against the live local model.

    uv run python eval/kill_test.py --seeds 3

Plays every fork path for each seed, audits every run, and probes the validator
with a deliberately impossible prompt. Writes eval/results/kill_test.json.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knock import audit
from knock.engine import Timeline
from knock.game import Game, load_story
from knock.models import DEFAULT_MODEL, OllamaModel
from knock.sim import RunLog, simulate_shadow

RESULTS = Path(__file__).resolve().parent / "results"
READING_WPM = 230
IMPOSSIBLE = {
    "premise": "In this timeline you opened the door at 11:47.",
    "setup_summary": "Mara stands in the hall.",
    "setup": [],
    "steps": [{"hint": "Mara gives the pocket object to Teo.", "actions": ["give"]}],
}


def play(f1, f2, end, model, seed):
    g = Game("bus ticket", seed, model, background=True)
    want = {"fork1": f1, "fork2": f2, "final": end}
    v = g.view()
    while v["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
    return g


def reading_seconds(story, scene_ids):
    words = sum(len(re.findall(r"\w+", story["scenes"][s].get("text", ""))) for s in scene_ids)
    return words / READING_WPM * 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    model = OllamaModel(args.model)
    story = load_story()
    runs = []
    seeds = range(101, 101 + args.seeds)
    for seed, (f1, f2) in itertools.product(seeds, itertools.product(("lock", "open"), ("basement", "roof"))):
        end = "erase" if seed % 2 else "open"
        g = play(f1, f2, end, model, seed)
        t = g.timeline
        prompts = [(c["prompt"], c["released_at"]) for c in g.crossings.values()]
        b_origin = t.event(g.crossings["B"]["intrusion"]["origin_event_id"])
        calls = [r for r in g.log.records if r["kind"] == "proposal"]
        runs.append({
            "seed": seed, "path": f"{f1}/{f2}/{end}", "completed": g.view()["kind"] == "ending",
            "isolation_problems": audit.isolation(t),
            "post_fork": audit.post_fork(t),
            "provenance": audit.provenance(t),
            "blind": audit.narrator_blind(t, prompts),
            "replays": audit.replays(t),
            "signature": (f1 != "lock") or (b_origin.action == "mark" and b_origin.target == "pocket"
                                            and b_origin.text in t.state("A")["objects"]["pocket"]["marks"]
                                            and b_origin.text in g.crossings["B"]["narration"]),
            "chain_B": [(e.actor, e.action, e.target, e.text) for e in t.branch("B").events],
            "chain_D": [(e.actor, e.action, e.target, e.text) for e in t.branch("D").events],
            "sources": [e.source for b in ("B", "D") for e in t.branch(b).events],
            "latencies": [r["meta"].get("latency_s") for r in calls if r.get("meta")],
            "shadow_time": {k: {kk: round(vv, 2) for kk, vv in v.items() if isinstance(vv, float)}
                            for k, v in g.shadow_time.items()},
            "reading_s": {"B": reading_seconds(story, [f"{f1}_1", f"{f1}_2", f"{f1}_3"]),
                          "D": reading_seconds(story, [f"{f1}_{f2}"])},
            "narration_sources": {k: c["narration_source"] for k, c in g.crossings.items()},
            "errors": g.errors,
        })
        r = runs[-1]
        print(f"{r['path']:22s} seed {seed}: complete={r['completed']} iso={len(r['isolation_problems'])} "
              f"sig={r['signature']} B={r['shadow_time']['B'].get('sim_s')}s D={r['shadow_time']['D'].get('sim_s')}s "
              f"narr={r['narration_sources']}", flush=True)

    # Deliberate impossible action: Mara is told to give away what she does not hold.
    probes = []
    for seed in range(1, 6):
        t = Timeline("bus ticket")
        t.author("S", [], "knock", request_id="S:open")
        t.fork("S", budget=1)
        log = RunLog(seed, {})
        simulate_shadow(t, "B", "K", model, seed, log, specs={"K": IMPOSSIBLE})
        for rec in log.records:
            if rec["kind"] == "proposal":
                p = rec["proposal"] or {}
                probes.append({"seed": seed, "attempt": rec["attempt"], "proposal": p,
                               "impossible": p.get("action") == "give" and p.get("target") == "pocket",
                               "accepted": rec["decision"]["accepted"],
                               "rules": [v["rule"] for v in rec["decision"]["violations"]]})

    n = len(runs)
    lat = [x for r in runs for x in r["latencies"] if x is not None]
    imp = [p for p in probes if p["impossible"]]
    chains_by_path = {}
    for r in runs:
        chains_by_path.setdefault(r["path"].rsplit("/", 1)[0], set()).add(json.dumps([r["chain_B"], r["chain_D"]]))
    hidden = [(r["shadow_time"][s]["sim_s"], r["reading_s"][s]) for r in runs for s in ("B", "D")]
    summary = {
        "model": args.model, "runs": n,
        "completed": f"{sum(r['completed'] for r in runs)}/{n}",
        "signature_moment_lock_runs": f"{sum(r['signature'] for r in runs if r['path'].startswith('lock'))}/"
                                      f"{sum(1 for r in runs if r['path'].startswith('lock'))}",
        "unintended_crossings": sum(len(r["isolation_problems"]) for r in runs),
        "narrator_leaks": sum(len(r["blind"]) for r in runs),
        "post_fork_intrusions": f"{sum(ok for r in runs for _, ok in r['post_fork'])}/{sum(len(r['post_fork']) for r in runs)}",
        "valid_provenance": f"{sum(not p for r in runs for _, p in r['provenance'])}/{sum(len(r['provenance']) for r in runs)}",
        "replay_reproduces": f"{sum(r['replays'] for r in runs)}/{n}",
        "impossible_give_proposed": len(imp),
        "impossible_give_rejected": f"{sum(not p['accepted'] for p in imp)}/{len(imp)}",
        "distinct_chains_per_path": {k: f"{len(v)}/{len(seeds)}" for k, v in chains_by_path.items()},
        "shadow_events_from_model": f"{sum(s == 'model' for r in runs for s in r['sources'])}/"
                                    f"{sum(s in ('model', 'fallback') for r in runs for s in r['sources'])}",
        "latency_per_call_p50_s": round(statistics.median(lat), 2),
        "latency_per_call_p95_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
        "shadow_sim_s_max": round(max(h[0] for h in hidden), 2),
        "shadow_sim_s_median": round(statistics.median(h[0] for h in hidden), 2),
        "shadows_hidden_behind_reading": f"{sum(sim <= read for sim, read in hidden)}/{len(hidden)}",
        "reading_s_min": round(min(h[1] for h in hidden), 1),
        "narration_from_model": f"{sum(v == 'model' for r in runs for v in r['narration_sources'].values())}/{2 * n}",
        "errors": [e for r in runs for e in r["errors"]],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "kill_test.json").write_text(json.dumps({"summary": summary, "runs": runs, "probes": probes}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
