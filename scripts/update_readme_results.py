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
- The crossing text is hand-written around the committed facts. The atmosphere-sentence rows measure only the model's optional extra sentence, which is rule-checked. That doesn't prove the sentence never contradicts anything; hand labels go in `eval/labels/` and show as "not labeled" until a person fills them in.
- Time from the click to the crossing, with no reading pause at all: p50 {p['crossing_scene_p50_s']} s, p95 {p['crossing_scene_p95_s']} s. In real play the shadow runs while you read the scenes before it."""
    hosted_md = R / "hosted" / "summary.md"
    hosted = ""
    if hosted_md.exists():
        hosted = """### Hosted model, reported separately

These numbers come from a small separate run of the same engine, with `gemma-4-31B-it` (Gemma 4 31B, open-weight) on DigitalOcean serverless inference. It's a bigger model than the local default, so none of these numbers feed the headline results above. 8 playthroughs, 2 seeds per fork path, `uv run python eval/run_all.py --hosted --seeds 2`, raw logs in `eval/results/hosted/runs/`, spend ledger in `eval/results/spend.json`.

""" + body(hosted_md).replace("# Evaluation summary", "").strip() + """

The ledger in `spend.json` also covers health checks, probes and earlier runs. Two seeds per path is too few to compare models; this run shows only that hosted mode works end to end and holds the same invariants."""
    readme = (ROOT / "README.md").read_text()
    readme = re.sub(r"<!-- RESULTS:BEGIN -->.*<!-- RESULTS:END -->",
                    "<!-- RESULTS:BEGIN -->\n" + local + "\n<!-- RESULTS:END -->", readme, flags=re.S)
    readme = re.sub(r"### Hosted model, reported separately\n.*?(?=\n\nEvery number above comes from)",
                    hosted, readme, flags=re.S)
    (ROOT / "README.md").write_text(readme)


if __name__ == "__main__":
    main()
