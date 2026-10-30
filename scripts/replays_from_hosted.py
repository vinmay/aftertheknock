"""Copy the published replays from the hosted eval runs (no model calls).

    uv run python scripts/replays_from_hosted.py

One night per route pair, chosen for distinct pocket objects and word marks. Each is re-run through
the engine and must reproduce its recorded state hashes; only the derived ending text is refreshed. Local runs stay in eval/results/runs only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knock.game import replay, verify_replay  # noqa: E402

HOSTED = ROOT / "eval" / "results" / "hosted" / "runs"
OUT = ROOT / "data" / "runs" / "replays"
CHOSEN = ["lock_roof_erase_1001", "lock_basement_open_1002", "open_basement_open_1001", "open_roof_open_1002"]

if __name__ == "__main__":
    for old in OUT.glob("*.json"):
        old.unlink()
    for name in CHOSEN:
        rec = json.loads((HOSTED / f"{name}.json").read_text())
        g = replay(rec)
        if not verify_replay(g, rec):
            raise SystemExit(f"{name} does not reproduce its recorded state")
        # The ending is display text derived from committed state; refresh it to the current wording.
        # The log, model outputs and state hashes are copied unchanged.
        rec["ending"] = json.loads(json.dumps(g.ending))
        (OUT / f"{name}.json").write_text(json.dumps(rec))
        print(OUT / f"{name}.json", rec["pocket"], rec["model"]["model"])
