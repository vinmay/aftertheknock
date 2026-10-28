# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 140/162 (86%) |
| Shadow events committed from the model | 157/162 (97%) |
| Shadow events from hand-written fallback | 5/162 (3%) |
| Proposals rejected by the validator | 33/190 (17%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 3/48 (6%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.62 s / 2.44 s |
| Whole shadow timeline p50 / p95 | 6.32 s / 8.64 s |
| Model calls per playthrough (mean) | 10.0 |
| Tokens per playthrough (mean) | 6042 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.28 |
| lock/roof | 6 | 6 | 0.27 |
| open/basement | 6 | 6 | 0.47 |
| open/roof | 6 | 6 | 0.43 |

Rejections by rule: R_NOOP 15, R_TYPE 9, R_REACH 5, R_BEAT 4.

Freedom vs. guarantees, per shadow step (committed by the model or by a hand-written fallback; distinct action types and distinct texts across runs). Crossing 2 written by the player: 24/24 (100%).

| Shadow | Step | Model | Fallback | Skipped | Actions | Distinct texts |
|---|---|---|---|---|---|---|
| B.lock | 1 | 12 | 0 | 0 | open | 0 |
| B.lock | 2 | 12 | 0 | 0 | move | 0 |
| B.lock | 3 | 7 | 5 | 0 | give, speak | 7 |
| B.lock | 4 | 12 | 0 | 0 | mark | 9 |
| B.open | 1 | 12 | 0 | 0 | speak | 10 |
| B.open | 2 | 12 | 0 | 0 | take | 0 |
| B.open | 3 | 12 | 0 | 0 | mark | 10 |
| D.lock.basement | 1 | 6 | 0 | 0 | speak | 3 |
| D.lock.basement | 2 | 6 | 0 | 0 | take | 0 |
| D.lock.basement | 3 | 6 | 0 | 0 | mark | 4 |
| D.lock.roof | 1 | 6 | 0 | 0 | move | 0 |
| D.lock.roof | 2 | 6 | 0 | 0 | speak | 4 |
| D.lock.roof | 3 | 6 | 0 | 0 | give, take | 0 |
| D.lock.roof | 4 | 6 | 0 | 0 | mark | 3 |
| D.open.basement | 1 | 6 | 0 | 0 | open | 0 |
| D.open.basement | 2 | 6 | 0 | 0 | move | 0 |
| D.open.basement | 3 | 6 | 0 | 0 | mark | 4 |
| D.open.roof | 1 | 6 | 0 | 0 | speak | 4 |
| D.open.roof | 2 | 6 | 0 | 0 | give | 0 |
| D.open.roof | 3 | 6 | 0 | 0 | mark | 1 |
