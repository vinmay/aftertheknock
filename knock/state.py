"""World state: plain dicts, changed only by applying committed deltas.

A delta is a list of ops:
    ["set", "objects.pocket.holder", "mara"]
    ["append", "objects.pocket.marks", "4913"]
Paths are always three parts: <kind>.<id>.<field>.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORY_DIR = ROOT / "data" / "story"

CHAR_DEFAULTS = {"status": "present", "said": []}
OBJECT_DEFAULTS = {"holder": None, "location": None, "marks": [], "markable": False,
                   "hidden": False, "opens": None, "found_by": None}
PLACE_DEFAULTS = {"locked": False, "key": None, "adjacent": []}

# Characters in these states take no actions and cannot be acted on socially.
INACTIVE = {"dead", "departed"}


def load_world(path: Path | None = None) -> dict:
    with open(path or STORY_DIR / "world.toml", "rb") as f:
        return tomllib.load(f)


def initial_state(world: dict | None = None, pocket_name: str | None = None) -> dict:
    world = world or load_world()
    state = {"chars": {}, "objects": {}, "places": {}}
    for cid, c in world["chars"].items():
        state["chars"][cid] = {**copy.deepcopy(CHAR_DEFAULTS), **c}
    for oid, o in world["objects"].items():
        state["objects"][oid] = {**copy.deepcopy(OBJECT_DEFAULTS), **o}
    for pid, p in world["places"].items():
        state["places"][pid] = {**copy.deepcopy(PLACE_DEFAULTS), **p}
    if pocket_name:
        state["objects"]["pocket"]["name"] = pocket_name
    return state


def split_path(path: str) -> tuple[str, str, str]:
    parts = path.split(".")
    if len(parts) != 3:
        raise ValueError(f"bad state path {path!r}")
    return parts[0], parts[1], parts[2]


def get(state: dict, path: str):
    kind, eid, field = split_path(path)
    return state[kind][eid][field]


def apply_delta(state: dict, delta) -> dict:
    """Return a new state with the delta applied. The input is never mutated."""
    new = copy.deepcopy(state)
    for op, path, value in delta:
        kind, eid, field = split_path(path)
        entity = new[kind][eid]
        if op == "set":
            entity[field] = value
        elif op == "append":
            entity[field] = [*entity[field], value]
        else:
            raise ValueError(f"unknown op {op!r}")
    return new


def fact_holds(state: dict, op: str, path: str, value) -> bool:
    current = get(state, path)
    return value in current if op == "append" else current == value


def state_hash(state: dict) -> str:
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def name_of(state: dict, eid: str) -> str:
    for kind in ("chars", "objects", "places"):
        if eid in state[kind]:
            return state[kind][eid]["name"]
    return eid


def kind_of(state: dict, eid: str) -> str | None:
    for kind in ("chars", "objects", "places"):
        if eid in state[kind]:
            return kind
    return None


def location_of(state: dict, eid: str) -> str | None:
    """Where a character or object physically is (objects follow their holder)."""
    if eid in state["chars"]:
        return state["chars"][eid]["location"]
    if eid in state["objects"]:
        obj = state["objects"][eid]
        if obj["holder"]:
            return state["chars"][obj["holder"]]["location"]
        return obj["location"]
    return None
