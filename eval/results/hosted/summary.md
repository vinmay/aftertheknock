# Evaluation summary

Model: gemma-4-31B-it provider-managed, 8 live playthroughs (every fork path), raw logs in `eval/results/hosted/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 16/16 (100%) |
| Intrusions with valid origin event | 16/16 (100%) |
| Replays reproducing every state hash | 8/8 (100%) |
| Shadow events accepted on the model's first proposal | 53/54 (98%) |
| Shadow events committed from the model | 54/54 (100%) |
| Shadow events from hand-written fallback | 0/54 (0%) |
| Proposals rejected by the validator | 1/55 (2%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 5/16 (31%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 14/16 (88%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.42 s / 1.81 s |
| Whole shadow timeline p50 / p95 | 5.1 s / 6.22 s |
| Model calls per playthrough (mean) | 9.2 |
| Tokens per playthrough (mean) | 5636 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 2 | 2 | 0.08 |
| lock/roof | 2 | 2 | 0.0 |
| open/basement | 2 | 2 | 0.08 |
| open/roof | 2 | 2 | 0.27 |

Rejections by rule: R_BEAT 1.

Freedom vs. guarantees, per shadow step (committed by the model or by a hand-written fallback; distinct action types and distinct texts across runs). Crossing 2 written by the player: 8/8 (100%).

| Shadow | Step | Model | Fallback | Skipped | Actions | Distinct texts |
|---|---|---|---|---|---|---|
| B.lock | 1 | 4 | 0 | 0 | open | 2 |
| B.lock | 2 | 4 | 0 | 0 | move | 0 |
| B.lock | 3 | 4 | 0 | 0 | speak | 2 |
| B.lock | 4 | 4 | 0 | 0 | mark | 4 |
| B.open | 1 | 4 | 0 | 0 | speak | 4 |
| B.open | 2 | 4 | 0 | 0 | give, take | 4 |
| B.open | 3 | 4 | 0 | 0 | mark | 4 |
| D.lock.basement | 1 | 2 | 0 | 0 | speak | 2 |
| D.lock.basement | 2 | 2 | 0 | 0 | give | 2 |
| D.lock.basement | 3 | 2 | 0 | 0 | mark | 2 |
| D.lock.roof | 1 | 2 | 0 | 0 | move | 0 |
| D.lock.roof | 2 | 2 | 0 | 0 | speak | 2 |
| D.lock.roof | 3 | 2 | 0 | 0 | give | 2 |
| D.lock.roof | 4 | 2 | 0 | 0 | mark | 2 |
| D.open.basement | 1 | 2 | 0 | 0 | open | 2 |
| D.open.basement | 2 | 2 | 0 | 0 | move | 0 |
| D.open.basement | 3 | 2 | 0 | 0 | mark | 2 |
| D.open.roof | 1 | 2 | 0 | 0 | speak | 2 |
| D.open.roof | 2 | 2 | 0 | 0 | give | 2 |
| D.open.roof | 3 | 2 | 0 | 0 | mark | 2 |

Spend: $0.00906 over 8 playthroughs (mean $0.00113 each) at $0.18/$0.5 per 1M input/output tokens.
