# Models

## Choice

| | |
|---|---|
| **Model** | `gemma4:e2b` (Gemma 4 E2B) via Ollama |
| **Quantization** | Q4_K_M (GGUF) |
| **License** | Apache 2.0 (as shipped in the Ollama model manifest) |
| **Source** | Official Ollama library tag `gemma4:e2b` (`registry.ollama.ai/library/gemma4:e2b`) |
| **Manifest digest** | `sha256:b37049369adfe3d2b653af0ab301a062ca5cbe96ace6aa0d3f2559ee9b563fc2` |
| **Weights blob** | `sha256:6dd7ac24fe13a238898e16e253ebc7a72fa27fb83f2658c2f6c241df3af5937d` (3.50 GB) |
| **Runtime** | Ollama 0.40.4, JSON-schema constrained output (`format`), `think: false`, `num_ctx` 2048 |
| **Hardware** | NVIDIA GTX 1660 Ti, 6 GB VRAM (Turing, no BF16) |

Override with `KNOCK_MODEL=gemma4:e4b`.

### Provenance check (2026-10-27)

The local model is byte-identical to the official library build:

- The sha256 of the registry manifest (`GET https://registry.ollama.ai/v2/library/gemma4/manifests/e2b`, fetched 2026-10-27) is `b37049369adf…63fc2`, the same digest Ollama reports locally (`/api/tags`).
- Ollama stores that registry manifest as a blob, and the stored copy is byte-identical to the one fetched today. Its layers (model weights, vision projector, a small draft model used for speculative decoding with `draft_num_predict 3`, template, license, params) all have the same digests and sizes.
- The weights file on disk hashes to its digest, `6dd7ac24…5937d` (checked with `sha256sum`).
- The license layer is the Apache License 2.0.
- `ollama show` reports a parent named `gemma4-validation-20260929:e2b-imatrix-attention-q6-q8-draft2`. That name comes from the official manifest's own `from` fields, so it is the library build's metadata, not a local custom build.

Check it yourself:

```bash
curl -s -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  https://registry.ollama.ai/v2/library/gemma4/manifests/e2b | sha256sum
curl -s localhost:11434/api/tags | grep -A2 '"gemma4:e2b"'
```

The game sends `temperature 0.8` and a seed with every call, which override the manifest's `temperature 1`.

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
