# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 132/156 (85%) |
| Shadow events committed from the model | 148/156 (95%) |
| Shadow events from hand-written fallback | 8/156 (5%) |
| Proposals rejected by the validator | 41/189 (22%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 1/48 (2%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.58 s / 2.16 s |
| Whole shadow timeline p50 / p95 | 5.8 s / 8.63 s |
| Model calls per playthrough (mean) | 9.9 |
| Tokens per playthrough (mean) | 5694 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.23 |
| lock/roof | 6 | 6 | 0.31 |
| open/basement | 6 | 6 | 0.3 |
| open/roof | 6 | 6 | 0.41 |

Rejections by rule: R_ADJACENT 18, R_TYPE 7, R_REACH 6, R_BEAT 5, R_NOOP 5.
