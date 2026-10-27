# After the Knock

*(working title)*

A short horror story where the choice you didn't make keeps happening in a hidden timeline, and its events start appearing in yours as evidence you can trace.

**The idea:** a horror game that simulates rejected choices as separate worlds and lets their recorded events manifest as evidence in the player's chosen reality.

Branching horror, timelines that remember, and AI-generated branching stories all exist already (see [Prior art](#prior-art)). This project combines three things: rejected branches that keep simulating, intrusions that carry facts created after the fork, and inspectable provenance for every intrusion.

It is 11:47 PM. A storm. A woman you have never met knocks on your door and says, *"Please. You already let me in."* Lock the door, and later you find four digits written on the thing in your pocket. Mara wrote them, in the night where you opened the door. Click the digits and the game shows you the event that wrote them.

<!-- GIF: owner records from scripts/demo -->

## Run it

You need [uv](https://docs.astral.sh/uv/) and, for live play, [Ollama](https://ollama.com) with a GPU that has about 4 GB free.

```bash
scripts/setup            # uv sync, pull gemma4:e2b if missing, run the tests
uv run knock-server      # open http://localhost:8047
```

No GPU? Recorded runs play without a model:

```bash
uv run knock-server --replay-only
```

Terminal version: `uv run knock play`.

### What runs where

| Mode | What happens | Needs |
|---|---|---|
| **Live** (`knock-server`) | Shadow timelines are generated during your playthrough by Gemma 4 E2B on your machine. | Ollama + `gemma4:e2b` |
| **Replay** (`--replay-only`, or the list on the title screen) | A recorded real run. The engine re-runs it from the recorded model outputs and checks that every timeline's state hash matches the recording. Labeled *replay* on screen. Nothing is generated. | Nothing |
| **Evaluation** (`eval/run_all.py`) | Fresh live runs, or recomputation from the saved raw logs with `--from-results`. | Ollama for fresh runs |

No closed-model API is called anywhere.

## How it works

```mermaid
flowchart LR
  P[Player choice] -->|fork| C[Canonical timeline]
  P -->|rejected option| S[Shadow timeline]
  S -->|state, hint, JSON schema| G[Gemma 4 E2B]
  G -->|proposed event| V{Validator}
  V -->|rejected + reason| G
  V -->|accepted| E[Engine commits event]
  E --> S
  E -->|one committed event| X[Intrusion]
  X -->|released facts only| N[Blind narrator]
  N --> C
  X -.->|origin event id| M[Map / trace]
```

**Branching rule.** Fork 1 (open or lock the door) creates your timeline A and a shadow B. Fork 2 (basement or roof) splits A into your timeline C and a shadow D. B and D are the only shadows. Each runs a fixed budget of 3 or 4 events, then freezes. Your timeline receives exactly one intrusion from each.

**The model proposes; the engine commits.** For each shadow event, Gemma gets the shadow's current state and a one-line story beat, and must answer in a JSON schema: actor, action (one of move, speak, give, take, mark, open, hide, discover, harm), target, recipient, text, summary. It never writes state. A deterministic validator checks the proposal against the shadow's state: people and things exist in that branch, you can only give, mark or hide what you hold, you must be in the same place, moves go one step to an adjacent place, locked places need their key, the dead and departed don't act. If the proposal passes, the engine derives the state change from the action's rules and commits it. If it fails, the model gets the reason and tries again, up to three times. After that a hand-written fallback event is committed. Every rule has a unit test.

**Intrusions cite their origin.** An intrusion links one committed shadow event to a change in your timeline. The engine refuses an intrusion unless the origin is a committed, simulated event of that shadow and it reveals at least one fact that did not exist at the fork, such as the digits that were written on your object. Echoing your own input back does not count. The map draws a thread from every intrusion to its origin event. Click either end to see the proposal, every rejected attempt, the validator's decision and the facts that crossed.

**The narrator is blind.** The model that writes the moment of an intrusion sees only your timeline's state and the facts that intrusion released. It never sees shadow logs, so it cannot leak anything early. Its text is checked by rules: the inscription must appear exactly, no invented numbers or capitalised words, no character who isn't there, the object must be named. It gets one retry, then hand-written text is used. These checks cover the critical facts. They do not prove that the prose is free of every contradiction (see the hand-labeled numbers below).

**Reproducible.** Each run records the seed, model and quantization, a hash of every prompt, every proposal and validator decision, and every committed event. Replaying a log through the engine reproduces every timeline's state hash. The test suite checks this, and checks that a tampered log fails.

## Results

<!-- RESULTS:BEGIN -->
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

Notes on these numbers:

- A *shadow step* is one shadow event: up to three model proposals, then a hand-written fallback. 2 of 156 steps were skipped (the model failed and no fallback fitted the state). Those shadows still ended with a committed inscription.
- "Rejected" counts proposals stopped by the validator or by a story-beat check (`R_BEAT`). A rejected proposal never changes state.
- Narration checks are rules on the critical facts (the inscription, numbers, names, the object), not a proof that the prose never contradicts anything. Hand labels go in `eval/labels/` and show as "not labeled" until a person fills them.
- Time from the click to the crossing text, with no reading pause at all: p50 6.92 s, p95 9.19 s. In real play the shadow runs while you read the two or three scenes before it.
<!-- RESULTS:END -->

Every number above comes from `uv run python eval/run_all.py` (or `--from-results` to recompute from the committed raw logs). Charts: `eval/results/chart_*.svg`. Model choice and the day-one benchmark: [MODELS.md](MODELS.md).

## Why open models

- **Many background calls.** Each playthrough makes about 10 model calls (about 5,600 tokens) that the player never sees directly, and most of them happen in a world the player never visits. Running locally, they cost nothing and need no account.
- **Pinned weights.** The validator, the prompts and the fallback rates were tuned against one exact model file (digest in MODELS.md). A hosted model that changes underneath would silently change how often the story falls back.
- **Inspectable.** The whole simulation runs on your machine: model, prompts, proposals, rejections and commits. The evidence view (press `E`) shows all of it.

## Tests

```bash
uv run pytest
```

Covered: every validator rule, the branching rule, idempotent retries, branch isolation, the post-fork-fact rule, the provenance invariant, the blind narrator, the impossible-action rejection, replay reproduction and tamper detection, narration fallback, and all eight story paths with a model that never answers usefully.

## Layout

```
knock/      engine.py (timelines, forks, intrusions), validator.py, sim.py (shadow simulation),
            narrator.py (blind narrator), game.py (scenes, endings), audit.py (invariants),
            models.py (Ollama, replay), api.py (HTTP server), cli.py
web/        browser game, map, trace drawer, evidence view
data/story/ world, scenes, endings, shadow beats and fallback events (hand-written)
data/runs/replays/  recorded runs for replay mode
eval/       run_all.py, kill_test.py, bench_models.py, results/
scripts/    setup, demo
```

## Prior art

Rejected branches, alternate timelines and memory across branches are well explored in games, tools and research. This project is related work, not a first. What it adds is one combination: rejected branches that keep simulating, intrusions that carry facts created after the fork, and inspectable provenance for every intrusion.

- *Undertale* (Toby Fox, 2015): the game remembers what you did in runs you reset.
- *Slay the Princess* (Black Tabby Games, 2023): branching horror where earlier choices reshape later loops.
- *Until Dawn* (Supermassive Games, 2015): branching horror with consequences tracked across a night.
- [ECHO](https://github.com/kishore1035/ECHO): a voice-command game where rewriting reality splits the world into alternate branches, and one character remembers the erased timelines. It uses Google Gemini to parse commands.
- [BRING](https://github.com/Eva-E1/BRING) (Apache-2.0): an LLM role-play framework that can create, switch and merge branches for alternate storylines.
- WHAT-IF (arXiv [2412.10582](https://arxiv.org/abs/2412.10582)): branching interactive fiction generated with LLMs.
- Narrative Studio (arXiv [2504.02426](https://arxiv.org/abs/2504.02426)): exploring branching story trees with LLMs.

## Credits

- Model: Gemma 4 E2B (Google), Apache 2.0, run through [Ollama](https://ollama.com).
- No third-party code is vendored. The web front end and the sound (generated with the Web Audio API) are written for this project.
- All characters and places are fictional.

## Commits after deadline

None yet. Any commit after Oct 31, 2026, 11:59 PM PDT will be listed here.

## License

Apache 2.0. See [LICENSE](LICENSE).
