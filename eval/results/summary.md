# Evaluation summary

Model: gemma4:e2b Q4_K_M, 24 live playthroughs (every fork path), raw logs in `eval/results/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 48/48 (100%) |
| Intrusions with valid origin event | 48/48 (100%) |
| Replays reproducing every state hash | 24/24 (100%) |
| Shadow events accepted on the model's first proposal | 129/156 (83%) |
| Shadow events committed from the model | 141/156 (90%) |
| Shadow events from hand-written fallback | 13/156 (8%) |
| Proposals rejected by the validator | 58/199 (29%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Raw narration failing rule checks (first attempt) | 5/48 (10%) |
| Final narration from the model (rest is fallback) | 46/48 (96%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.61 s / 1.95 s |
| Whole shadow timeline p50 / p95 | 5.97 s / 8.39 s |
| Model calls per playthrough (mean) | 10.5 |
| Tokens per playthrough (mean) | 5588 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 6 | 6 | 0.29 |
| lock/roof | 6 | 6 | 0.25 |
| open/basement | 6 | 6 | 0.35 |
| open/roof | 6 | 6 | 0.4 |

Rejections by rule: R_NOOP 23, R_ADJACENT 18, R_BEAT 7, R_REACH 6, R_SELF 2, R_TYPE 2.
