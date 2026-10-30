"""Regenerate the README results sections from eval/results (never typed by hand).

    uv run python scripts/update_readme_results.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "eval" / "results"


def body(path: Path) -> str:
    return path.read_text().split("\n", 2)[2].strip()


def main() -> None:
    m = json.loads((R / "summary.json").read_text())
    s = m["shadow_steps"]
    skipped = int(s["skipped"].split("/")[0])
    p = m["performance"]
    local = body(R / "summary.md") + f"""

Notes on these numbers:

- A *shadow step* is one shadow event: up to three model proposals, then a hand-written fallback. {skipped} of {s['total']} steps {"was" if skipped == 1 else "were"} skipped (the model failed and no fallback fitted the state). Every shadow still ended with its committed crossing.
- "Rejected" counts proposals stopped by the validator or by a story-beat check (`R_BEAT`, which includes banned phrases). A rejected proposal never changes state.
- The crossing text is hand-written around the committed facts. The atmosphere-sentence row measures only the model's optional extra sentence, which is rule-checked. That doesn't prove the sentence never contradicts anything; hand labels go in `eval/labels/` and show as "not labeled" until a person fills them in.
- Time from the click to the crossing, with no reading pause at all: p50 {p['crossing_scene_p50_s']} s, p95 {p['crossing_scene_p95_s']} s. In real play the shadow runs while you read the scenes before it."""
    if "freedom" in m:
        fr = m["freedom"]
        model = sum(x["model"] for x in fr["steps"])
        total = sum(x["model"] + x["fallback"] + x["skipped"] for x in fr["steps"])
        texts = sum(x["distinct_texts"] for x in fr["steps"])
        local += f"""

### Freedom vs. guarantees

Each other night has fixed beats (who must end up holding what, and that a message gets written); the model decides how it happens and every word that's said or written.

Some steps are narrower than others, on purpose:

- **Teo's door.** The step where Teo opens his apartment for Mara accepts only his door as the target. (The hosted model kept "opening" the hall.)
- **Word marks.** Each message the other you writes gets a seeded meaning (an apology, a warning, a promise, an admission, …), and the prompt asks for a verb. A phrase made only of names or things ("MARA", "KEY TAG") is rejected.
- **The key.** In the other you's night, the step before the last mark guarantees only that you end up holding the brass key. Whether Mara gives it or you take it is the model's choice.

The model-commit rate below is higher than in earlier runs partly because these steps are narrower: there are fewer wrong answers to make.

Measured over these runs: {model} of {total} shadow steps were committed from the model's own proposal; counted step by step, it produced {texts} distinct spoken or written texts; crossing 2 was written in the player's own hand in {fr['crossing2_written_by_player']} runs. The per-step table above (Freedom vs. guarantees) gives model/fallback counts, the action types the model chose, and how many different texts it produced for each step."""
    hosted_md = R / "hosted" / "summary.md"
    hosted = ""
    if hosted_md.exists():
        hosted = """### Hosted model, reported separately

These numbers come from a small separate run of the same engine, with `gemma-4-31B-it` (Gemma 4 31B, open-weight) on DigitalOcean serverless inference. It's a bigger model than the local default, so none of these numbers feed the headline results above. 8 playthroughs, 2 seeds per fork path, `uv run python eval/run_all.py --hosted --seeds 2`, raw logs in `eval/results/hosted/runs/`, spend ledger in `eval/results/spend.json`.

""" + body(hosted_md).replace("# Evaluation summary", "").strip() + """

The ledger in `spend.json` also covers health checks, probes and earlier runs. Two seeds per path is too few to compare models; this run shows only that hosted mode works end to end and holds the same invariants."""
        hm = json.loads((R / "hosted" / "summary.json").read_text())
        never = [f"{x['shadow']} step {x['step']} (0/{x['model'] + x['fallback'] + x['skipped']})"
                 for x in hm.get("freedom", {}).get("steps", []) if x["model"] == 0]
        if never:
            hosted += ("\n\nSteps the hosted model never committed itself in this run: " + ", ".join(never)
                       + ". Each still happened, through its hand-written fallback; the per-step table shows the counts, "
                       "and the raw proposals and rejection reasons are in the run logs.")
    readme = (ROOT / "README.md").read_text()
    readme = re.sub(r"<!-- RESULTS:BEGIN -->.*<!-- RESULTS:END -->",
                    "<!-- RESULTS:BEGIN -->\n" + local + "\n<!-- RESULTS:END -->", readme, flags=re.S)
    readme = re.sub(r"### Hosted model, reported separately\n.*?(?=\n\nEvery number above comes from)",
                    hosted, readme, flags=re.S)
    (ROOT / "README.md").write_text(readme)


if __name__ == "__main__":
    main()
