# Evaluation summary

Model: gemma-4-31B-it provider-managed, 8 live playthroughs (every fork path), raw logs in `eval/results/hosted/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 16/16 (100%) |
| Intrusions with valid origin event | 16/16 (100%) |
| Replays reproducing every state hash | 8/8 (100%) |
| Shadow events accepted on the model's first proposal | 47/54 (87%) |
| Shadow events committed from the model | 50/54 (93%) |
| Shadow events from hand-written fallback | 4/54 (7%) |
| Proposals rejected by the validator | 15/65 (23%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 0/16 (0%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 16/16 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.41 s / 2.22 s |
| Whole shadow timeline p50 / p95 | 5.63 s / 8.84 s |
| Model calls per playthrough (mean) | 10.1 |
| Tokens per playthrough (mean) | 6128 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 2 | 2 | 0.17 |
| lock/roof | 2 | 2 | 0.2 |
| open/basement | 2 | 2 | 0.27 |
| open/roof | 2 | 2 | 0.27 |

Rejections by rule: R_NOOP 12, R_BEAT 3.

Freedom vs. guarantees, per shadow step (committed by the model or by a hand-written fallback; distinct action types and distinct texts across runs). Crossing 2 written by the player: 8/8 (100%).

| Shadow | Step | Model | Fallback | Skipped | Actions | Distinct texts |
|---|---|---|---|---|---|---|
| B.lock | 1 | 0 | 4 | 0 | open | 0 |
| B.lock | 2 | 4 | 0 | 0 | move | 0 |
| B.lock | 3 | 4 | 0 | 0 | speak | 3 |
| B.lock | 4 | 4 | 0 | 0 | mark | 3 |
| B.open | 1 | 4 | 0 | 0 | speak | 4 |
| B.open | 2 | 4 | 0 | 0 | take | 3 |
| B.open | 3 | 4 | 0 | 0 | mark | 3 |
| D.lock.basement | 1 | 2 | 0 | 0 | speak | 2 |
| D.lock.basement | 2 | 2 | 0 | 0 | give | 2 |
| D.lock.basement | 3 | 2 | 0 | 0 | mark | 1 |
| D.lock.roof | 1 | 2 | 0 | 0 | move | 0 |
| D.lock.roof | 2 | 2 | 0 | 0 | speak | 2 |
| D.lock.roof | 3 | 2 | 0 | 0 | give | 2 |
| D.lock.roof | 4 | 2 | 0 | 0 | mark | 2 |
| D.open.basement | 1 | 2 | 0 | 0 | open | 2 |
| D.open.basement | 2 | 2 | 0 | 0 | move | 0 |
| D.open.basement | 3 | 2 | 0 | 0 | mark | 2 |
| D.open.roof | 1 | 2 | 0 | 0 | speak | 2 |
| D.open.roof | 2 | 2 | 0 | 0 | give | 2 |
| D.open.roof | 3 | 2 | 0 | 0 | mark | 1 |

Spend: $0.00989 over 8 playthroughs (mean $0.00124 each) at $0.18/$0.5 per 1M input/output tokens.
