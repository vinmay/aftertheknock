# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 141/156 (90%) |
| Shadow events committed from the model | 146/156 (94%) |
| Shadow events from hand-written fallback | 7/156 (4%) |
| Proposals rejected by the validator | 37/183 (20%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 0/48 (0%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.65 s / 2.16 s |
| Whole shadow timeline p50 / p95 | 6.12 s / 8.3 s |
| Model calls per playthrough (mean) | 9.6 |
| Tokens per playthrough (mean) | 5393 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.22 |
| lock/roof | 6 | 6 | 0.23 |
| open/basement | 6 | 6 | 0.28 |
| open/roof | 6 | 6 | 0.41 |

Rejections by rule: R_ADJACENT 18, R_NOOP 11, R_REACH 6, R_TYPE 2.
