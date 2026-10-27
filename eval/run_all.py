"""Every number in the README and post comes from here.

    uv run python eval/run_all.py                    # generate live runs, then compute
    uv run python eval/run_all.py --from-results     # recompute from saved raw runs (no model)

Live generation plays every fork path for each seed with the local model and saves each
full run log to eval/results/runs/. Metrics are then computed only from those files and
written to eval/results/summary.json, summary.md and charts.

Hand labels (filled by a person, never by this script):
    eval/labels/intrusions.csv   does the intrusion's detail match the shadow event?
    eval/labels/narration.csv    does the raw / final narration contradict the committed facts?
Missing labels are reported as "not labeled", never guessed.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knock import audit
from knock.engine import Timeline
from knock.game import Game, replay, verify_replay
from knock.models import DEFAULT_MODEL, OllamaModel, load_dotenv, model_from_env
from knock.sim import RunLog, simulate_shadow

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RUNS = RESULTS / "runs"
LABELS = HERE / "labels"
POCKETS = ["bus ticket", "folded receipt", "house key", "guitar pick", "photo of my sister", "lighter",
           "wedding ring", "train ticket"]
IMPOSSIBLE = {
    "premise": "In this timeline you opened the door at 11:47.",
    "setup_summary": "Mara stands in the hall.",
    "setup": [],
    "steps": [{"hint": "Mara gives the pocket object to Teo.", "actions": ["give"]}],
}


# ---------------------------------------------------------------- generation
def play(f1, f2, end, model, seed, pocket):
    run_id = f"{f1}_{f2}_{end}_{seed}"
    if hasattr(model, "for_run"):
        model = model.for_run(run_id)
    g = Game(pocket, seed, model, background=True, run_id=run_id)
    want = {"fork1": f1, "fork2": f2, "final": end}
    v = g.view()
    timings = []
    while v["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        idx = next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0)
        t0 = time.perf_counter()
        v = g.choose(idx)
        timings.append({"to": g.scene, "s": round(time.perf_counter() - t0, 3)})
    rec = g.record()
    rec["choose_timings"] = timings
    rec["plan"] = {"f1": f1, "f2": f2, "end": end}
    return rec


def generate(model, seeds: int) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0)
    for seed in range(1, seeds + 1):
        for f1, f2 in itertools.product(("lock", "open"), ("basement", "roof")):
            end = rng.choice(["erase", "open"])
            pocket = rng.choice(POCKETS)
            rec = play(f1, f2, end, model, 1000 + seed, pocket)
            (RUNS / f"{f1}_{f2}_{end}_{1000 + seed}.json").write_text(json.dumps(rec))
            print(f"seed {1000 + seed} {f1}/{f2}/{end}: {rec['crossings']['B']['intrusion']['post_fork']} "
                  f"{rec['crossings']['D']['intrusion']['post_fork']}", flush=True)
    probes = []
    for seed in range(1, 11):
        t = Timeline("the bus ticket")
        t.author("S", [], "knock", request_id="S:open")
        t.fork("S", budget=1)
        log = RunLog(seed, model.info())
        simulate_shadow(t, "B", "K", model, seed, log, specs={"K": IMPOSSIBLE})
        probes += [r for r in log.records if r["kind"] == "proposal"]
    (RESULTS / "impossible_probes.json").write_text(json.dumps(probes, indent=1))


# ---------------------------------------------------------------- metrics
def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "0/0"


def quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))] if xs else None


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def read_labels(name):
    p = LABELS / name
    if not p.exists():
        return []
    with open(p) as f:
        return [r for r in csv.DictReader(f) if (r.get("label") or "").strip()]


def compute() -> dict:
    files = sorted(RUNS.glob("*.json"))
    recs = [json.loads(p.read_text()) for p in files]
    n = len(recs)
    m: dict = {"runs": n, "model": recs[0]["model"] if recs else None}

    iso, post, prov, blind, reprod = [], [], [], [], []
    rules = Counter()
    calls = steps_model = steps_fb = steps_skip = 0
    first_try = steps_total = 0
    lat, sim_s, cross_s, tokens_per_run, calls_per_run = [], [], [], [], []
    narr_raw_fail = narr_final_model = narr_total = 0
    narr_raw_problem_kinds = Counter()
    chains = {}
    for rec in recs:
        t = replay(rec)
        reprod.append(verify_replay(t, rec))
        tl = t.timeline
        iso += audit.isolation(tl)
        post += [ok for _, ok in audit.post_fork(tl)]
        prov += [not p for _, p in audit.provenance(tl)]
        blind += audit.narrator_blind(tl, [(c["prompt"], c["released_at"]) for c in rec["crossings"].values()])
        props = [r for r in rec["log"] if r["kind"] == "proposal"]
        calls += len(props)
        calls_per_run.append(len(props) + sum(1 for r in rec["log"] if r["kind"] == "narration"))
        tokens_per_run.append(sum((r.get("meta") or {}).get("prompt_tokens", 0) + (r.get("meta") or {}).get("output_tokens", 0)
                                  for r in rec["log"] if r["kind"] in ("proposal", "narration")))
        for r in props:
            for v in r["decision"]["violations"]:
                rules[v["rule"]] += 1
            if (r.get("meta") or {}).get("latency_s") is not None:
                lat.append(r["meta"]["latency_s"])
        by_step = {}
        for r in rec["log"]:
            if r["kind"] in ("proposal", "fallback", "skipped"):
                by_step.setdefault((r["branch"], r["step"]), []).append(r)
        for recs_ in by_step.values():
            steps_total += 1
            last = recs_[-1]["kind"]
            steps_model += last == "proposal"
            steps_fb += last == "fallback"
            steps_skip += last == "skipped"
            first_try += recs_[0]["kind"] == "proposal" and recs_[0]["decision"]["accepted"]
        for k, st in rec["shadow_time"].items():
            if "sim_s" in st:
                sim_s.append(st["sim_s"])
        cross_s += [x["s"] for x in rec["choose_timings"] if x["to"].startswith("crossing")]
        for c in rec["crossings"].values():
            narr_total += 1
            narr_raw_fail += bool(c["raw"]["problems"])
            narr_final_model += c["narration_source"] == "model"
            for p in c["raw"]["problems"]:
                narr_raw_problem_kinds[p.split(":")[0]] += 1
        key = f"{rec['fork1']}/{rec['fork2']}"
        chain = [(e["actor"], e["action"], e["target"], e["text"]) for b in ("B", "D")
                 for e in rec["timeline"]["branches"][b]["events"] if e["source"] in ("model", "fallback")]
        chains.setdefault(key, []).append(tuple(map(tuple, chain)))

    m["isolation"] = {"unintended_crossings": len(iso), "narrator_leaks": len(blind), "details": iso + blind}
    m["post_fork_evidence"] = pct(sum(post), len(post))
    m["provenance"] = pct(sum(prov), len(prov))
    m["replay_reproduces"] = pct(sum(reprod), n)
    m["shadow_steps"] = {"total": steps_total, "first_try": pct(first_try, steps_total),
                         "model_committed": pct(steps_model, steps_total), "fallback": pct(steps_fb, steps_total),
                         "skipped": pct(steps_skip, steps_total)}
    m["validator"] = {"proposals": calls, "rejections_by_rule": dict(rules.most_common()),
                      "rejected": pct(sum(1 for rec in recs for r in rec["log"] if r["kind"] == "proposal"
                                          and not r["decision"]["accepted"]), calls)}
    probes_path = RESULTS / "impossible_probes.json"
    if probes_path.exists():
        probes = json.loads(probes_path.read_text())
        imp = [p for p in probes if (p.get("proposal") or {}).get("action") == "give"
               and (p.get("proposal") or {}).get("target") == "pocket"]
        m["impossible_give"] = {"probe_calls": len(probes), "impossible_proposed": len(imp),
                                "rejected": pct(sum(not p["decision"]["accepted"] for p in imp), len(imp)),
                                "rules": dict(Counter(v["rule"] for p in imp for v in p["decision"]["violations"]))}
    m["narration"] = {"crossings": narr_total,
                      "raw_first_attempt_failed_rule_checks": pct(narr_raw_fail, narr_total),
                      "raw_failure_kinds": dict(narr_raw_problem_kinds),
                      "final_text_from_model": pct(narr_final_model, narr_total),
                      "final_text_fallback": pct(narr_total - narr_final_model, narr_total)}
    lab = read_labels("narration.csv")
    m["narration"]["hand_labeled"] = (
        {"raw_contradicts": pct(sum(r["label"] == "contradicts" for r in lab if r["which"] == "raw"),
                                sum(r["which"] == "raw" for r in lab)),
         "final_contradicts": pct(sum(r["label"] == "contradicts" for r in lab if r["which"] == "final"),
                                  sum(r["which"] == "final" for r in lab))} if lab else "not labeled")
    ilab = read_labels("intrusions.csv")
    m["intrusion_accuracy_hand_labeled"] = (pct(sum(r["label"] == "match" for r in ilab), len(ilab))
                                            if ilab else "not labeled")
    var = {}
    for key, cs in chains.items():
        pairs = list(itertools.combinations(cs, 2))
        var[key] = {"runs": len(cs), "distinct_chains": len(set(cs)),
                    "mean_pairwise_event_overlap_jaccard": round(statistics.mean(jaccard(a, b) for a, b in pairs), 2) if pairs else None}
    m["variation"] = var
    m["performance"] = {
        "model_call_latency_p50_s": round(quantile(lat, 0.5), 2), "model_call_latency_p95_s": round(quantile(lat, 0.95), 2),
        "shadow_sim_p50_s": round(quantile(sim_s, 0.5), 2), "shadow_sim_p95_s": round(quantile(sim_s, 0.95), 2),
        "crossing_scene_p50_s": round(quantile(cross_s, 0.5), 2), "crossing_scene_p95_s": round(quantile(cross_s, 0.95), 2),
        "crossing_note": "time from click to crossing text with no reading pause; includes waiting for the shadow",
        "model_calls_per_playthrough_mean": round(statistics.mean(calls_per_run), 1),
        "tokens_per_playthrough_mean": round(statistics.mean(tokens_per_run)),
    }
    return m


def write_label_sheets() -> None:
    """Sample sheets for a person to fill. Existing labels are never overwritten."""
    LABELS.mkdir(parents=True, exist_ok=True)
    recs = [json.loads(p.read_text()) for p in sorted(RUNS.glob("*.json"))]
    ip = LABELS / "intrusions.csv"
    if not ip.exists():
        rows = []
        for rec in recs:
            evs = {e["event_id"]: e for b in rec["timeline"]["branches"].values() for e in b["events"]}
            for shadow, c in rec["crossings"].items():
                o = evs[c["intrusion"]["origin_event_id"]]
                rows.append({"run": rec["run_id"], "intrusion": c["intrusion"]["intrusion_id"],
                             "shadow_event": f"{o['actor']} {o['action']} {o['target']} \"{o['text']}\"",
                             "shown_to_player": c["narration"], "label": "", "note": ""})
        random.Random(1).shuffle(rows)
        with open(ip, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows[:30])
    np_ = LABELS / "narration.csv"
    if not np_.exists():
        rows = []
        for rec in recs:
            evs = {e["event_id"]: e for b in rec["timeline"]["branches"].values() for e in b["events"]}
            for shadow, c in rec["crossings"].items():
                o = evs[c["intrusion"]["origin_event_id"]]
                facts = f"{o['target']} now reads \"{o['text']}\"; written by {o['actor']}"
                rows.append({"run": rec["run_id"], "intrusion": c["intrusion"]["intrusion_id"], "which": "raw",
                             "facts": facts, "text": c["raw"]["text"], "label": "", "note": ""})
                rows.append({"run": rec["run_id"], "intrusion": c["intrusion"]["intrusion_id"], "which": "final",
                             "facts": facts, "text": c["narration"], "label": "", "note": ""})
        with open(np_, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows[:60])


def bar_svg(title: str, items: list[tuple[str, float, str]], max_v: float, unit: str = "") -> str:
    h = 34 * len(items) + 50
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="{h}" font-family="ui-monospace, monospace" font-size="12">',
           '<rect width="100%" height="100%" fill="#0e0d0c"/>',
           f'<text x="16" y="24" fill="#e6dfd2" font-size="14">{title}</text>']
    for i, (label, v, color) in enumerate(items):
        y = 44 + i * 34
        w = 0 if not max_v else 360 * v / max_v
        out += [f'<text x="16" y="{y + 15}" fill="#8d867b">{label}</text>',
                f'<rect x="230" y="{y}" width="{w:.1f}" height="20" fill="{color}"/>',
                f'<text x="{236 + w:.1f}" y="{y + 15}" fill="#e6dfd2">{v:g}{unit}</text>']
    out.append("</svg>")
    return "\n".join(out)


def charts(m: dict) -> None:
    s = m["shadow_steps"]
    num = lambda x: int(x.split("/")[0])  # noqa: E731
    (RESULTS / "chart_steps.svg").write_text(bar_svg(
        f"Shadow events: who decided ({s['total']} steps)",
        [("model, first proposal", num(s["first_try"]), "#e6dfd2"),
         ("model, after retry", num(s["model_committed"]) - num(s["first_try"]), "#8d867b"),
         ("hand-written fallback", num(s["fallback"]), "#c7a55b"),
         ("skipped", num(s["skipped"]), "#b23a2f")], s["total"]))
    rules = m["validator"]["rejections_by_rule"]
    (RESULTS / "chart_rejections.svg").write_text(bar_svg(
        f"Validator rejections by rule ({m['validator']['proposals']} proposals)",
        [(k, v, "#b23a2f") for k, v in rules.items()], max(rules.values()) if rules else 1))
    p = m["performance"]
    (RESULTS / "chart_latency.svg").write_text(bar_svg(
        "Latency on a GTX 1660 Ti (seconds)",
        [("model call p50", p["model_call_latency_p50_s"], "#e6dfd2"), ("model call p95", p["model_call_latency_p95_s"], "#8d867b"),
         ("shadow timeline p50", p["shadow_sim_p50_s"], "#7f8d99"), ("shadow timeline p95", p["shadow_sim_p95_s"], "#7f8d99")],
        max(p["shadow_sim_p95_s"], 1), " s"))


def markdown(m: dict) -> str:
    v = m["validator"]
    lines = [f"# Evaluation summary", "", f"Model: {m['model'].get('model')} {m['model'].get('quantization')}, "
             f"{m['runs']} live playthroughs (every fork path), raw logs in `eval/results/runs/`.", "",
             "| Measurement | Result |", "|---|---|",
             f"| Unintended state crossings | {m['isolation']['unintended_crossings']} |",
             f"| Narrator saw unreleased shadow text | {m['isolation']['narrator_leaks']} |",
             f"| Intrusions revealing a post-fork fact | {m['post_fork_evidence']} |",
             f"| Intrusions with valid origin event | {m['provenance']} |",
             f"| Replays reproducing every state hash | {m['replay_reproduces']} |",
             f"| Shadow events accepted on the model's first proposal | {m['shadow_steps']['first_try']} |",
             f"| Shadow events committed from the model | {m['shadow_steps']['model_committed']} |",
             f"| Shadow events from hand-written fallback | {m['shadow_steps']['fallback']} |",
             f"| Proposals rejected by the validator | {v['rejected']} |"]
    if "impossible_give" in m:
        ig = m["impossible_give"]
        lines.append(f"| Deliberate impossible give rejected | {ig['rejected']} |")
    nar = m["narration"]
    lines += [f"| Raw narration failing rule checks (first attempt) | {nar['raw_first_attempt_failed_rule_checks']} |",
              f"| Final narration from the model (rest is fallback) | {nar['final_text_from_model']} |",
              f"| Narration contradictions, hand-labeled | {nar['hand_labeled'] if isinstance(nar['hand_labeled'], str) else nar['hand_labeled']} |",
              f"| Intrusion accuracy, hand-labeled | {m['intrusion_accuracy_hand_labeled']} |"]
    p = m["performance"]
    lines += [f"| Model call latency p50 / p95 | {p['model_call_latency_p50_s']} s / {p['model_call_latency_p95_s']} s |",
              f"| Whole shadow timeline p50 / p95 | {p['shadow_sim_p50_s']} s / {p['shadow_sim_p95_s']} s |",
              f"| Model calls per playthrough (mean) | {p['model_calls_per_playthrough_mean']} |",
              f"| Tokens per playthrough (mean) | {p['tokens_per_playthrough_mean']} |", "",
              "Variation (same choices, different seeds):", "",
              "| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |", "|---|---|---|---|"]
    for k, x in m["variation"].items():
        lines.append(f"| {k} | {x['runs']} | {x['distinct_chains']} | {x['mean_pairwise_event_overlap_jaccard']} |")
    lines += ["", f"Rejections by rule: {', '.join(f'{k} {c}' for k, c in v['rejections_by_rule'].items())}."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-results", action="store_true", help="recompute from saved runs; no model calls")
    ap.add_argument("--seeds", type=int, default=6, help="seeds per fork path for live generation")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--hosted", action="store_true",
                    help="use the hosted provider from .env (KNOCK_PROVIDER=openai); results go to eval/results/hosted/")
    args = ap.parse_args()
    global RESULTS, RUNS
    if args.hosted:
        load_dotenv()
        RESULTS = HERE / "results" / "hosted"
        RUNS = RESULTS / "runs"
    if not args.from_results:
        model = model_from_env(args.model) if args.hosted else OllamaModel(args.model)
        if args.hosted and not getattr(model.info(), "get", lambda k: None)("hosted"):
            raise SystemExit("--hosted needs KNOCK_PROVIDER=openai and the KNOCK_OPENAI_* settings in .env")
        generate(model, args.seeds)
    m = compute()
    if not args.hosted:
        write_label_sheets()
    else:
        import os
        ledger = json.loads(Path(os.environ.get("KNOCK_SPEND_FILE", "eval/results/spend.json")).read_text())
        ids = {p.stem for p in RUNS.glob("*.json")}
        runs = [v for k, v in ledger["runs"].items() if k in ids]
        m["spend"] = {"playthroughs": len(runs), "usd_total": round(sum(r["usd"] for r in runs), 5),
                      "usd_per_playthrough_mean": round(statistics.mean(r["usd"] for r in runs), 5) if runs else None,
                      "price_in_per_m": ledger["price_in_per_m"], "price_out_per_m": ledger["price_out_per_m"],
                      "ledger_total_usd_all_calls": ledger["total_usd"]}
    charts(m)
    (RESULTS / "summary.json").write_text(json.dumps(m, indent=1))
    md = markdown(m)
    if "spend" in m:
        sp = m["spend"]
        md += (f"\nSpend: ${sp['usd_total']} over {sp['playthroughs']} playthroughs "
               f"(mean ${sp['usd_per_playthrough_mean']} each) at ${sp['price_in_per_m']}/"
               f"${sp['price_out_per_m']} per 1M input/output tokens.\n")
    (RESULTS / "summary.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
