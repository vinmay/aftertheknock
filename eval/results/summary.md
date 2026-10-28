# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 128/156 (82%) |
| Shadow events committed from the model | 146/156 (94%) |
| Shadow events from hand-written fallback | 10/156 (6%) |
| Proposals rejected by the validator | 49/195 (25%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 1/48 (2%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.83 s / 2.96 s |
| Whole shadow timeline p50 / p95 | 7.43 s / 12.58 s |
| Model calls per playthrough (mean) | 10.2 |
| Tokens per playthrough (mean) | 6143 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.26 |
| lock/roof | 6 | 6 | 0.32 |
| open/basement | 6 | 6 | 0.27 |
| open/roof | 6 | 6 | 0.44 |

Rejections by rule: R_ADJACENT 18, R_BEAT 9, R_NOOP 9, R_REACH 7, R_TYPE 6.
