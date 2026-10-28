# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 137/162 (85%) |
| Shadow events committed from the model | 152/162 (94%) |
| Shadow events from hand-written fallback | 10/162 (6%) |
| Proposals rejected by the validator | 46/198 (23%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 1/48 (2%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 48/48 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.62 s / 2.46 s |
| Whole shadow timeline p50 / p95 | 6.77 s / 8.95 s |
| Model calls per playthrough (mean) | 10.3 |
| Tokens per playthrough (mean) | 6260 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.51 |
| lock/roof | 6 | 6 | 0.3 |
| open/basement | 6 | 6 | 0.41 |
| open/roof | 6 | 6 | 0.5 |

Rejections by rule: R_ADJACENT 18, R_REACH 9, R_TYPE 8, R_BEAT 6, R_NOOP 6.
