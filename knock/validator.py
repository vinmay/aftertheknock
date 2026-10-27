"""Deterministic validation of proposed events.

The model only proposes {actor, action, target, to, text, summary}. This module
decides whether the proposal is possible in the branch's current state and, if
so, derives its preconditions and state delta. Nothing the model writes becomes
state except through the delta computed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .state import INACTIVE, kind_of

ACTIONS = ("move", "speak", "give", "take", "mark", "open", "hide", "discover", "harm")
PROPOSAL_FIELDS = ("actor", "action", "target", "to", "text", "summary")
MAX_MARK = 24
MAX_SPEECH = 120


@dataclass
class Decision:
    accepted: bool
    violations: list[tuple[str, str]] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    pre_paths: list[str] = field(default_factory=list)
    delta: list[list] = field(default_factory=list)

    @property
    def rules(self) -> list[str]:
        return [r for r, _ in self.violations]

    def to_dict(self) -> dict:
        return {"accepted": self.accepted,
                "violations": [{"rule": r, "detail": d} for r, d in self.violations],
                "preconditions": self.preconditions}


class _Check:
    def __init__(self):
        self.violations: list[tuple[str, str]] = []
        self.pre: list[str] = []
        self.paths: list[str] = []

    def fail(self, rule: str, detail: str) -> None:
        self.violations.append((rule, detail))

    def need(self, ok: bool, rule: str, detail: str, path: str | None = None, desc: str | None = None) -> bool:
        if not ok:
            self.fail(rule, detail)
            return False
        if path:
            self.paths.append(path)
        if desc:
            self.pre.append(desc)
        return True


def _normalize(p: dict) -> dict:
    out = {}
    for k in PROPOSAL_FIELDS:
        v = p.get(k, "")
        out[k] = v.strip() if isinstance(v, str) else v
    return out


def validate(state: dict, proposal: dict, *, frozen: bool = False) -> Decision:
    c = _Check()
    if frozen:
        c.fail("R_FROZEN", "branch is frozen; no further events")
        return Decision(False, c.violations)
    missing = [k for k in ("actor", "action", "target") if not isinstance(proposal.get(k), str) or not proposal.get(k)]
    if missing:
        c.fail("R_SCHEMA", f"missing fields: {', '.join(missing)}")
        return Decision(False, c.violations)
    p = _normalize(proposal)
    actor, action, target, to, text = p["actor"], p["action"], p["target"], p["to"], p["text"]
    if action not in ACTIONS:
        c.fail("R_SCHEMA", f"unknown action {action!r}")
        return Decision(False, c.violations)

    chars, objects, places = state["chars"], state["objects"], state["places"]

    # Existence in this branch.
    if actor not in chars:
        c.fail("R_EXISTS", f"actor {actor!r} does not exist in this branch")
    if kind_of(state, target) is None:
        c.fail("R_EXISTS", f"target {target!r} does not exist in this branch")
    if action == "give" and to and to not in chars:
        c.fail("R_EXISTS", f"recipient {to!r} does not exist in this branch")
    if c.violations:
        return Decision(False, c.violations)

    a = chars[actor]
    here = a["location"]
    c.need(a["status"] not in INACTIVE, "R_ACTIVE", f"{actor} is {a['status']} and cannot act",
           f"chars.{actor}.status", f"{actor} is {a['status']}")
    if c.violations:
        return Decision(False, c.violations)
    c.paths.append(f"chars.{actor}.location")

    tkind = kind_of(state, target)
    delta: list[list] = []

    def need_kind(kind: str) -> bool:
        return c.need(tkind == kind, "R_TYPE", f"{action} needs a {kind[:-1]} target, got {tkind[:-1]} {target!r}")

    def holds(obj: str) -> bool:
        return c.need(objects[obj]["holder"] == actor, "R_HOLDS",
                      f"{actor} does not hold {obj} (holder: {objects[obj]['holder']})",
                      f"objects.{obj}.holder", f"{actor} holds {obj}")

    def colocated_char(cid: str, rule_target: str = "R_TARGET_ACTIVE") -> bool:
        ok = c.need(cid != actor, "R_SELF", f"{actor} cannot {action} themself")
        ok = ok and c.need(chars[cid]["location"] == here, "R_REACH",
                           f"{cid} is in {chars[cid]['location']}, {actor} is in {here}",
                           f"chars.{cid}.location", f"{cid} is in {here}")
        ok = ok and c.need(chars[cid]["status"] not in INACTIVE, rule_target,
                           f"{cid} is {chars[cid]['status']}", f"chars.{cid}.status")
        return ok

    if action == "move":
        if need_kind("places"):
            place = places[target]
            if c.need(target != here, "R_NOOP", f"{actor} is already in {target}") and \
               c.need(target in places[here]["adjacent"], "R_ADJACENT",
                      f"{target} is not adjacent to {here}", None, f"{here} adjoins {target}"):
                if place["locked"]:
                    key = place["key"]
                    if c.need(key is not None and objects[key]["holder"] == actor, "R_LOCKED",
                              f"{target} is locked and {actor} does not hold its key",
                              f"objects.{key}.holder" if key else f"places.{target}.locked",
                              f"{actor} holds {key}"):
                        delta.append(["set", f"places.{target}.locked", False])
                else:
                    c.paths.append(f"places.{target}.locked")
                    c.pre.append(f"{target} is unlocked")
                delta.append(["set", f"chars.{actor}.location", target])

    elif action == "speak":
        aloud = tkind == "places" and target == here
        if aloud:
            c.pre.append(f"{actor} speaks aloud in {here}")
        if aloud or (need_kind("chars") and colocated_char(target)):
            if c.need(bool(text), "R_TEXT", "speak needs text") and \
               c.need(len(text) <= MAX_SPEECH, "R_TEXT", f"speech longer than {MAX_SPEECH} chars"):
                delta.append(["append", f"chars.{actor}.said", text])

    elif action == "give":
        if need_kind("objects") and holds(target):
            if c.need(bool(to), "R_SCHEMA", "give needs a recipient in 'to'") and colocated_char(to):
                delta.append(["set", f"objects.{target}.holder", to])

    elif action == "take":
        if need_kind("objects"):
            obj = objects[target]
            holder = obj["holder"]
            if holder == actor:
                c.fail("R_NOOP", f"{actor} already holds {target}")
            elif holder is not None:
                if c.need(chars[holder]["location"] == here, "R_REACH",
                          f"{target} is with {holder} in {chars[holder]['location']}, {actor} is in {here}",
                          f"objects.{target}.holder", f"{holder} holds {target} in {here}"):
                    delta.append(["set", f"objects.{target}.holder", actor])
            else:
                if c.need(obj["location"] == here, "R_REACH", f"{target} is not in {here}",
                          f"objects.{target}.location", f"{target} lies in {here}") and \
                   c.need(not obj["hidden"] or obj["found_by"] == actor, "R_HIDDEN",
                          f"{target} is hidden; it must be discovered first"):
                    delta += [["set", f"objects.{target}.holder", actor],
                              ["set", f"objects.{target}.location", None],
                              ["set", f"objects.{target}.hidden", False]]

    elif action == "mark":
        if need_kind("objects") and holds(target):
            ok = c.need(objects[target]["markable"], "R_TYPE", f"{target} cannot be marked")
            ok = ok and c.need(bool(text), "R_TEXT", "mark needs text")
            ok = ok and c.need(len(text) <= MAX_MARK and text.isprintable(), "R_TEXT",
                               f"mark text must be printable and at most {MAX_MARK} chars")
            if ok:
                delta.append(["append", f"objects.{target}.marks", text])

    elif action == "open":
        if need_kind("places"):
            place = places[target]
            if c.need(place["locked"], "R_NOOP", f"{target} is not locked",
                      f"places.{target}.locked", f"{target} is locked"):
                inside = here == target
                if inside:
                    c.pre.append(f"{actor} is inside {target}")
                elif c.need(target in places[here]["adjacent"], "R_REACH", f"{actor} is not at {target}"):
                    key = place["key"]
                    c.need(key is not None and objects[key]["holder"] == actor, "R_LOCKED",
                           f"{target} is locked from the other side and {actor} has no key",
                           f"objects.{key}.holder" if key else None, f"{actor} holds {key}")
                if not c.violations:
                    delta.append(["set", f"places.{target}.locked", False])

    elif action == "hide":
        if need_kind("objects") and holds(target):
            delta += [["set", f"objects.{target}.holder", None],
                      ["set", f"objects.{target}.location", here],
                      ["set", f"objects.{target}.hidden", True]]

    elif action == "discover":
        if need_kind("objects"):
            obj = objects[target]
            if c.need(obj["holder"] is None and obj["location"] == here, "R_REACH",
                      f"{target} is not lying in {here}", f"objects.{target}.location", f"{target} lies in {here}") and \
               c.need(obj["found_by"] != actor, "R_NOOP", f"{actor} already found {target}"):
                delta += [["set", f"objects.{target}.hidden", False],
                          ["set", f"objects.{target}.found_by", actor]]

    elif action == "harm":
        if need_kind("chars") and colocated_char(target):
            nxt = "dead" if chars[target]["status"] == "hurt" else "hurt"
            delta.append(["set", f"chars.{target}.status", nxt])

    if c.violations:
        return Decision(False, c.violations)
    return Decision(True, [], c.pre, sorted(set(c.paths)), delta)

