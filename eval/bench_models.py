"""Day-one model benchmark: schema validity, validator acceptance and latency per call.

    uv run python eval/bench_models.py gemma4:e2b gemma4:e4b --seeds 5

Runs both B shadows (door opened / door kept locked) for each seed and model and
records every call. Results go to eval/results/bench_<model>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knock.engine import Timeline
from knock.models import OllamaModel
from knock.sim import RunLog, simulate_shadow

RESULTS = Path(__file__).resolve().parent / "results"


def schema_valid(obj, schema) -> bool:
    if not isinstance(obj, dict):
        return False
    for k in schema["required"]:
        if k not in obj:
            return False
    for k, spec in schema["properties"].items():
        v = obj.get(k)
        if "enum" in spec and v not in spec["enum"]:
            return False
        if spec.get("type") == "string":
            if not isinstance(v, str):
                return False
            if "maxLength" in spec and len(v) > spec["maxLength"]:
                return False
            if "pattern" in spec and not re.fullmatch(spec["pattern"], v):
                return False
    return True


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))
    return xs[i]


def run(model_name: str, seeds: int) -> dict:
    model = OllamaModel(model_name)
    info = model.info()
    # Warm the model so load time is not counted as call latency.
    t0 = time.perf_counter()
    model.generate([{"role": "user", "content": "Say ok."}], None, max_tokens=4)
    load_s = time.perf_counter() - t0

    calls, steps = [], []
    for seed in range(1, seeds + 1):
        for key in ("B.open", "B.lock"):
            t = Timeline("a bus ticket")
            t.author("S", [], "A knock at 11:47.", request_id="S:open")
            t.fork("S", budget=4)
            log = RunLog(seed, info)
            start = len(calls)
            simulate_shadow(t, "B", key, _SchemaRecorder(model, calls), seed, log)
            proposals = [r for r in log.records if r["kind"] == "proposal"]
            for c, r in zip(calls[start:], proposals, strict=True):
                c.update(spec=key, step=r["step"], prompt_hash=r["prompt_hash"],
                         accepted=r["decision"]["accepted"],
                         rules=[v["rule"] for v in r["decision"]["violations"]],
                         proposal=r["proposal"])
            by_step = {}
            for r in log.records:
                by_step.setdefault(r["step"], []).append(r)
            for i, recs in by_step.items():
                steps.append({"seed": seed, "spec": key, "step": i,
                              "attempts": sum(1 for r in recs if r["kind"] == "proposal"),
                              "outcome": recs[-1]["kind"] if recs[-1]["kind"] != "proposal" else "model",
                              "first_ok": recs[0]["kind"] == "proposal" and recs[0]["decision"]["accepted"]})

    lat = [c["latency_s"] for c in calls if "latency_s" in c]
    n = len(calls)
    summary = {
        "model": info, "load_s": round(load_s, 2), "seeds": seeds, "calls": n,
        "json_valid": sum(c["json_ok"] for c in calls) / n,
        "schema_valid": sum(c["schema_ok"] for c in calls) / n,
        "validator_accept_per_call": sum(c.get("accepted", False) for c in calls) / n,
        "steps": len(steps),
        "steps_first_try": sum(s["first_ok"] for s in steps) / len(steps),
        "steps_model_committed": sum(s["outcome"] == "model" for s in steps) / len(steps),
        "steps_fallback": sum(s["outcome"] == "fallback" for s in steps) / len(steps),
        "latency_p50_s": round(statistics.median(lat), 3),
        "latency_p95_s": round(pct(lat, 0.95), 3),
        "output_tokens_mean": round(statistics.mean(c["output_tokens"] for c in calls), 1),
        "prompt_tokens_mean": round(statistics.mean(c["prompt_tokens"] for c in calls), 1),
    }
    return {"summary": summary, "calls": calls, "steps": steps}


class _SchemaRecorder:
    """Wraps a model and records schema validity and latency of every call."""

    def __init__(self, model, sink):
        self.model, self.sink = model, sink

    def generate(self, messages, schema=None, seed=0, max_tokens=200):
        r = self.model.generate(messages, schema, seed=seed, max_tokens=max_tokens)
        try:
            obj = json.loads(r.text)
            json_ok = True
        except json.JSONDecodeError:
            obj, json_ok = None, False
        self.sink.append({"seed": seed, "latency_s": r.latency_s, "json_ok": json_ok,
                          "schema_ok": json_ok and schema_valid(obj, schema),
                          "prompt_tokens": r.prompt_tokens, "output_tokens": r.output_tokens})
        return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--seeds", type=int, default=5)
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name in args.models:
        res = run(name, args.seeds)
        out = RESULTS / f"bench_{name.replace(':', '_')}.json"
        out.write_text(json.dumps(res, indent=1))
        print(json.dumps(res["summary"], indent=1))


if __name__ == "__main__":
    main()
