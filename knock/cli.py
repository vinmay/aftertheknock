"""Terminal front end.

    uv run knock play                     interactive
    uv run knock auto lock basement erase --seed 3 --pocket "bus ticket"
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import time
from pathlib import Path

from .game import Game
from .models import DEFAULT_MODEL, OllamaModel, RandomModel
from .state import ROOT

RUNS = ROOT / "data" / "runs"


def show(v: dict, width: int = 72) -> None:
    print()
    for para in v["text"].split("\n\n"):
        print(textwrap.fill(para.strip(), width))
        print()
    if v["kind"] == "crossing":
        o = v["trace"]["origin"]
        print(f"   [trace] {v['trace']['intrusion']['intrusion_id']} <- {o['event_id']} "
              f"({o['actor']} {o['action']} {o['target']} \"{o['text']}\", source: {o['source']})")
    for i, c in enumerate(v["choices"]):
        print(f"  {i + 1}. {c}")


def pick(game: Game, plan: dict) -> int:
    sc = game.story["scenes"][game.scene]
    want = {"fork1": plan["f1"], "fork2": plan["f2"], "final": plan["end"]}.get(sc.get("kind"))
    for i, c in enumerate(sc.get("choices", [])):
        if want is not None and want in (c.get("fork"), c.get("ending")):
            return i
    return 0


def save(game: Game, tag: str = "") -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{tag}{game.run_id}.json"
    path.write_text(json.dumps(game.record(), indent=1))
    return path


def make_model(name: str):
    return RandomModel() if name == "random" else OllamaModel(name)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="knock")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play")
    p.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    p.add_argument("--model", default=DEFAULT_MODEL)
    a = sub.add_parser("auto")
    a.add_argument("f1", choices=["lock", "open"])
    a.add_argument("f2", choices=["basement", "roof"])
    a.add_argument("end", choices=["erase", "open"])
    a.add_argument("--seed", type=int, default=1)
    a.add_argument("--pocket", default="ticket stub")
    a.add_argument("--model", default=DEFAULT_MODEL)
    a.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    model = make_model(args.model)
    if args.cmd == "play":
        pocket = input("Your hand finds something in your pocket. What is it? ")
        game = Game(pocket, args.seed, model)
        v = game.view()
        while True:
            show(v)
            if not v["choices"]:
                break
            while True:
                raw = input("> ").strip()
                if raw.isdigit() and 1 <= int(raw) <= len(v["choices"]):
                    break
            v = game.choose(int(raw) - 1)
        print(f"run log: {save(game)}")
        return

    plan = {"f1": args.f1, "f2": args.f2, "end": args.end}
    game = Game(args.pocket, args.seed, model)
    v = game.view()
    while True:
        if not args.quiet:
            show(v)
        if not v["choices"]:
            break
        v = game.choose(pick(game, plan))
    path = save(game, "auto_")
    print(f"run log: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
