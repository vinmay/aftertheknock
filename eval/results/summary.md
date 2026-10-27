# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 144/156 (92%) |
| Shadow events committed from the model | 147/156 (94%) |
| Shadow events from hand-written fallback | 7/156 (4%) |
| Proposals rejected by the validator | 30/177 (17%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 0/48 (0%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.67 s / 2.12 s |
| Whole shadow timeline p50 / p95 | 5.83 s / 7.97 s |
| Model calls per playthrough (mean) | 9.4 |
| Tokens per playthrough (mean) | 5215 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.22 |
| lock/roof | 6 | 6 | 0.24 |
| open/basement | 6 | 6 | 0.26 |
| open/roof | 6 | 6 | 0.42 |

Rejections by rule: R_ADJACENT 18, R_NOOP 9, R_REACH 2, R_TYPE 1.
