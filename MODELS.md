# Models

## Choice

| | |
|---|---|
| **Model** | `gemma4:e2b` (Gemma 4 E2B) via Ollama |
| **Quantization** | Q4_K_M (GGUF) |
| **License** | Apache 2.0 (as shipped in the Ollama model manifest) |
| **Local digest** | `b37049369adf` |
| **Runtime** | Ollama 0.40.4, JSON-schema constrained output (`format`), `think: false`, `num_ctx` 2048 |
| **Hardware** | NVIDIA GTX 1660 Ti, 6 GB VRAM (Turing, no BF16) |

Override with `KNOCK_MODEL=gemma4:e4b`.

## Day-one benchmark (2026-10-26)

`uv run python eval/bench_models.py gemma4:e2b gemma4:e4b --seeds 5`

Both shadow-B scenarios (door opened; door kept locked), 5 seeds each, the real event
schema and the real validator. A *step* is one shadow event: up to 3 model calls, then a
hand-written fallback. Raw data: `eval/results/bench_gemma4_e2b.json`, `eval/results/bench_gemma4_e4b.json`.

| | gemma4:e2b | gemma4:e4b |
|---|---|---|
| Calls | 42 | 38 |
| JSON valid | 42/42 (100%) | 38/38 (100%) |
| Schema valid | 42/42 (100%) | 38/38 (100%) |
| Validator accepted, per call | 34/42 (81%) | 35/38 (92%) |
| Steps accepted on first call | 30/35 (86%) | 32/35 (91%) |
| Steps committed from the model (any attempt) | 34/35 (97%) | 35/35 (100%) |
| Steps that needed the fallback | 1/35 (3%) | 0/35 |
| Latency per call, p50 / p95 | 1.60 s / 1.97 s | 2.97 s / 3.67 s |
| Cold load | 0.3 s (already resident) | 73 s |
| Mean prompt / output tokens | 543 / 60 | 538 / 54 |

e4b is a 6.6 GB file and does not fit in 6 GB of VRAM; it runs partly on the CPU.

**Why e2b:** it is the smallest Gemma 4 variant, it never produced invalid JSON or schema
violations, and its rejected proposals are caught by the validator and retried or replaced
by fallbacks, so story correctness never depends on the model being right. A 3–4 event
shadow costs roughly 5–8 s with e2b, which is hidden behind the scenes the player reads
while it runs. e4b is more often right first time but about twice as slow per call.

These figures are from a small first sample (35 steps per model). The full evaluation
(`eval/run_all.py`) re-measures them over more seeds and all six shadow scenarios.

## What changed during the benchmark

The first e2b pass committed only 20/35 steps from the model. The fixes, each re-measured:

1. Put `summary` first in the schema so the model states the event before filling fields,
   renamed `to` to `recipient`, and listed which kind of id each action targets.
   (The model had been putting the person in `target` for `give` and `open`.)
2. Listed, for each person, where they can move and who is with them.
3. Added per-step goals checked against the state the event would produce
   (for example "Mara and Teo end up in the same place").
4. Rejected trivial codes (`1234`, `4321`, repeated digits) at the four-digit mark step.
