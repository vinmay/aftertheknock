# Evaluation summary

Model: gemma-4-31B-it provider-managed, 8 live playthroughs (every fork path), raw logs in `eval/results/hosted/runs/`.

| Measurement | Result |
|---|---|
| Unintended state crossings | 0 |
| Narrator saw unreleased shadow text | 0 |
| Intrusions revealing a post-fork fact | 16/16 (100%) |
| Intrusions with valid origin event | 16/16 (100%) |
| Replays reproducing every state hash | 8/8 (100%) |
| Shadow events accepted on the model's first proposal | 44/52 (85%) |
| Shadow events committed from the model | 48/52 (92%) |
| Shadow events from hand-written fallback | 4/52 (8%) |
| Proposals rejected by the validator | 16/64 (25%) |
| Deliberate impossible give rejected | 30/30 (100%) |
| Model's atmosphere sentence failing rule checks (first attempt) | 0/16 (0%) |
| Crossings that kept a model sentence (rest: hand-written lines only) | 16/16 (100%) |
| Narration contradictions, hand-labeled | not labeled |
| Intrusion accuracy, hand-labeled | not labeled |
| Model call latency p50 / p95 | 1.67 s / 2.9 s |
| Whole shadow timeline p50 / p95 | 6.83 s / 11.81 s |
| Model calls per playthrough (mean) | 10 |
| Tokens per playthrough (mean) | 5616 |

Variation (same choices, different seeds):

| Path | Runs | Distinct event chains | Mean pairwise overlap (Jaccard) |
|---|---|---|---|
| lock/basement | 2 | 2 | 0.09 |
| lock/roof | 2 | 2 | 0.2 |
| open/basement | 2 | 2 | 0.27 |
| open/roof | 2 | 2 | 0.4 |

Rejections by rule: R_NOOP 12, R_ADJACENT 2, R_REACH 2.

Spend: $0.00911 over 8 playthroughs (mean $0.00114 each) at $0.18/$0.5 per 1M input/output tokens.
