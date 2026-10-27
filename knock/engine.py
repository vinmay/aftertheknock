"""Timelines, the branching rule, commits and intrusions.

Branch layout (fixed, see BRANCH_PLAN):
    S  shared opening, frozen at fork 1
    S -> A (canonical) + B (shadow)       fork 1
    A -> C (canonical) + D (shadow)       fork 2
B and D are the only shadows. Each simulates a fixed budget of events, then freezes.
Canonical timelines receive exactly one intrusion from their sibling shadow.
"""

from __future__ import annotations

import copy
import functools
import threading
from dataclasses import dataclass, field, asdict

from .state import apply_delta, fact_holds, initial_state, state_hash, location_of
from .validator import Decision, validate

BRANCH_PLAN = {"S": ("A", "B"), "A": ("C", "D")}
SIBLING = {"B": "A", "D": "C"}


class EngineError(Exception):
    pass


class BranchRuleError(EngineError):
    pass


class FrozenError(EngineError):
    pass


class RejectedError(EngineError):
    def __init__(self, decision: Decision):
        super().__init__("; ".join(f"{r}: {d}" for r, d in decision.violations))
        self.decision = decision


class IntrusionError(EngineError):
    pass


@dataclass(frozen=True)
class Event:
    event_id: str
    branch: str
    seq: int
    actor: str
    action: str
    target: str
    to: str
    text: str
    preconditions: tuple
    state_delta: tuple
    evidence_refs: tuple
    summary: str
    source: str            # model | fallback | story | intrusion
    request_id: str
    origin: str | None = None      # for intrusion manifestations: the shadow event id
    released: tuple = ()           # for intrusion manifestations: facts made visible

    def to_dict(self) -> dict:
        d = asdict(self)
        d["preconditions"] = list(self.preconditions)
        d["state_delta"] = [list(x) for x in self.state_delta]
        d["evidence_refs"] = list(self.evidence_refs)
        d["released"] = [list(x) for x in self.released]
        return d


@dataclass
class Branch:
    id: str
    kind: str                      # root | canonical | shadow
    parent: str | None
    fork_seq: int                  # lineage length at the fork
    base_state: dict
    base_writers: dict
    budget: int | None = None
    events: list[Event] = field(default_factory=list)
    frozen: bool = False
    state: dict = field(default_factory=dict)
    writers: dict = field(default_factory=dict)   # state path -> last event id that wrote it

    @property
    def simulated(self) -> int:
        return sum(1 for e in self.events if e.source in ("model", "fallback"))


@dataclass
class Intrusion:
    intrusion_id: str
    origin_event_id: str
    origin_branch: str
    target_branch: str
    released: list
    post_fork: list
    manifestation_event_id: str

    def to_dict(self) -> dict:
        return asdict(self)


def _locked(fn):
    """Shadows simulate on a background thread while the canonical timeline advances."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


def _delta_paths(delta) -> list[str]:
    return [path for _, path, _ in delta]


class Timeline:
    def __init__(self, pocket_name: str = "a ticket stub", world: dict | None = None):
        self.pocket_name = pocket_name
        start = initial_state(world, pocket_name)
        root = Branch("S", "root", None, 0, start, {}, state=copy.deepcopy(start))
        self.branches: dict[str, Branch] = {"S": root}
        self.intrusions: list[Intrusion] = []
        self.ops: list[dict] = []          # replayable record of everything that changed state
        self._world = world
        self._lock = threading.RLock()

    # ---- lookup -------------------------------------------------------
    def branch(self, bid: str) -> Branch:
        if bid not in self.branches:
            raise EngineError(f"no branch {bid!r}")
        return self.branches[bid]

    @_locked
    def state(self, bid: str) -> dict:
        return copy.deepcopy(self.branch(bid).state)

    def lineage(self, bid: str) -> list[Event]:
        b = self.branch(bid)
        if b.parent is None:
            return list(b.events)
        return self.lineage(b.parent)[: b.fork_seq] + list(b.events)

    def event(self, event_id: str) -> Event:
        for b in self.branches.values():
            for e in b.events:
                if e.event_id == event_id:
                    return e
        raise EngineError(f"no event {event_id!r}")

    def canonical(self) -> str:
        for bid in ("C", "A", "S"):
            if bid in self.branches and not self.branches[bid].frozen:
                return bid
        return "C" if "C" in self.branches else "A"

    # ---- branching ----------------------------------------------------
    @_locked
    def fork(self, parent: str, budget: int = 3) -> tuple[str, str]:
        if parent not in BRANCH_PLAN:
            raise BranchRuleError(f"branch {parent!r} may not fork")
        canon_id, shadow_id = BRANCH_PLAN[parent]
        if canon_id in self.branches:
            raise BranchRuleError(f"branch {parent!r} already forked")
        p = self.branch(parent)
        if p.frozen:
            raise BranchRuleError(f"branch {parent!r} is frozen")
        n = len(self.lineage(parent))
        for bid, kind in ((canon_id, "canonical"), (shadow_id, "shadow")):
            self.branches[bid] = Branch(
                bid, kind, parent, n, copy.deepcopy(p.state), dict(p.writers),
                budget=budget if kind == "shadow" else None,
                state=copy.deepcopy(p.state), writers=dict(p.writers))
        p.frozen = True
        self.ops.append({"op": "fork", "parent": parent, "budget": budget})
        return canon_id, shadow_id

    # ---- commits ------------------------------------------------------
    def _existing(self, b: Branch, request_id: str) -> Event | None:
        for e in b.events:
            if e.request_id == request_id:
                return e
        return None

    def _append(self, b: Branch, *, actor, action, target, to, text, pre, delta, evidence,
                summary, source, request_id, origin=None, released=()) -> Event:
        seq = len(self.lineage(b.id))
        ev = Event(f"{b.id}.{seq:02d}", b.id, seq, actor, action, target, to, text,
                   tuple(pre), tuple(tuple(x) for x in delta), tuple(evidence),
                   summary, source, request_id, origin, tuple(tuple(x) for x in released))
        b.state = apply_delta(b.state, delta)
        for path in _delta_paths(delta):
            b.writers[path] = ev.event_id
        b.events.append(ev)
        if b.kind == "shadow" and b.budget is not None and b.simulated >= b.budget:
            b.frozen = True
        return ev

    def check(self, bid: str, proposal: dict) -> Decision:
        b = self.branch(bid)
        return validate(b.state, proposal, frozen=b.frozen)

    @_locked
    def commit(self, bid: str, proposal: dict, *, source: str = "model", request_id: str) -> Event:
        """Validate and commit a proposal. Idempotent per request_id."""
        b = self.branch(bid)
        prior = self._existing(b, request_id)
        if prior is not None:
            return prior
        if b.kind == "shadow" and source not in ("model", "fallback", "story"):
            raise EngineError(f"source {source!r} cannot write to shadow {bid}")
        decision = validate(b.state, proposal, frozen=b.frozen)
        if not decision.accepted:
            raise RejectedError(decision)
        evidence = sorted({b.writers[p] for p in decision.pre_paths if p in b.writers})
        ev = self._append(b, actor=proposal["actor"], action=proposal["action"],
                          target=proposal["target"], to=proposal.get("to", "") or "",
                          text=proposal.get("text", "") or "", pre=decision.preconditions,
                          delta=decision.delta, evidence=evidence,
                          summary=proposal.get("summary", "") or "", source=source,
                          request_id=request_id)
        self.ops.append({"op": "commit", "branch": bid, "proposal": dict(proposal),
                         "source": source, "request_id": request_id})
        return ev

    @_locked
    def author(self, bid: str, delta: list, summary: str, *, request_id: str,
               actor: str = "story", target: str = "") -> Event:
        """Commit a hand-authored story effect (for example the fork choice itself)."""
        b = self.branch(bid)
        prior = self._existing(b, request_id)
        if prior is not None:
            return prior
        if b.frozen:
            raise FrozenError(f"branch {bid} is frozen")
        ev = self._append(b, actor=actor, action="story", target=target, to="", text="",
                          pre=[], delta=delta, evidence=[], summary=summary, source="story",
                          request_id=request_id)
        self.ops.append({"op": "author", "branch": bid, "delta": [list(x) for x in delta],
                         "summary": summary, "request_id": request_id, "actor": actor,
                         "target": target})
        return ev

    @_locked
    def freeze(self, bid: str) -> None:
        b = self.branch(bid)
        if b.kind != "shadow":
            raise BranchRuleError(f"only shadows freeze on demand; {bid} is {b.kind}")
        if not b.frozen:
            b.frozen = True
            self.ops.append({"op": "freeze", "branch": bid})

    # ---- intrusions ---------------------------------------------------
    @_locked
    def cross(self, shadow_id: str, origin_event_id: str, *, request_id: str | None = None) -> Intrusion:
        """Let one committed shadow event manifest in its sibling canonical timeline."""
        request_id = request_id or f"cross:{shadow_id}"
        for x in self.intrusions:
            if x.origin_branch == shadow_id:
                if self.event(x.manifestation_event_id).request_id == request_id:
                    return x
                raise IntrusionError(f"shadow {shadow_id} has already crossed")
        shadow = self.branch(shadow_id)
        if shadow.kind != "shadow":
            raise IntrusionError(f"{shadow_id} is not a shadow timeline")
        if not shadow.frozen:
            raise IntrusionError(f"shadow {shadow_id} is still simulating")
        origin = next((e for e in shadow.events if e.event_id == origin_event_id), None)
        if origin is None:
            raise IntrusionError(f"{origin_event_id} is not a committed event of {shadow_id}")
        if origin.source not in ("model", "fallback"):
            raise IntrusionError(f"{origin_event_id} is not a simulated event")
        target_id = SIBLING[shadow_id]
        target = self.branch(target_id)
        if target.frozen:
            raise IntrusionError(f"canonical {target_id} is frozen")

        post_fork = [list(f) for f in origin.state_delta
                     if not fact_holds(shadow.base_state, *f) and not fact_holds(target.state, *f)]
        if not post_fork:
            raise IntrusionError(f"{origin_event_id} created no fact after the fork")
        released = [list(f) for f in origin.state_delta] + [["set", f"events.{origin.event_id}.actor", origin.actor]]

        delta = self._manifest(target.state, post_fork)
        ev = self._append(target, actor="intrusion", action="manifest", target=origin.target,
                          to="", text="", pre=[f"{origin.event_id} committed in {shadow_id}"],
                          delta=delta, evidence=[origin.event_id],
                          summary=f"Something from timeline {shadow_id} arrives.", source="intrusion",
                          request_id=request_id, origin=origin.event_id, released=released)
        x = Intrusion(f"X{len(self.intrusions) + 1}", origin.event_id, shadow_id, target_id,
                      released, post_fork, ev.event_id)
        self.intrusions.append(x)
        self.ops.append({"op": "cross", "shadow": shadow_id, "origin": origin.event_id,
                         "request_id": request_id})
        return x

    @staticmethod
    def _manifest(canon: dict, facts: list) -> list:
        """Map released shadow facts onto canonical state.

        Only marks change canonical state: the same mark appears on the same object.
        If that object is not within the player's reach, it arrives where the player is.
        Other facts (who held what, who went where) are revealed but change nothing.
        """
        delta = []
        player_loc = canon["chars"]["player"]["location"]
        for op, path, value in facts:
            kind, oid, fld = path.split(".")
            if kind == "objects" and fld == "marks" and oid in canon["objects"]:
                delta.append(["append", path, value])
                if location_of(canon, oid) != player_loc:
                    delta += [["set", f"objects.{oid}.holder", None],
                              ["set", f"objects.{oid}.location", player_loc],
                              ["set", f"objects.{oid}.hidden", False]]
        return delta

    def released_facts(self, bid: str) -> list:
        """Facts a canonical timeline is allowed to know about its shadows."""
        out = []
        for e in self.lineage(bid):
            if e.source == "intrusion":
                out += [list(f) for f in e.released]
        return out

    # ---- integrity ----------------------------------------------------
    def replay_state(self, bid: str) -> dict:
        s = initial_state(self._world, self.pocket_name)
        for e in self.lineage(bid):
            s = apply_delta(s, e.state_delta)
        return s

    def hashes(self) -> dict:
        return {bid: state_hash(b.state) for bid, b in self.branches.items()}

    @_locked
    def to_dict(self) -> dict:
        return {
            "pocket_name": self.pocket_name,
            "branches": {bid: {"kind": b.kind, "parent": b.parent, "fork_seq": b.fork_seq,
                               "budget": b.budget, "frozen": b.frozen,
                               "events": [e.to_dict() for e in b.events],
                               "state_hash": state_hash(b.state)}
                         for bid, b in self.branches.items()},
            "intrusions": [x.to_dict() for x in self.intrusions],
            "ops": self.ops,
        }

    @classmethod
    def replay(cls, ops: list[dict], pocket_name: str, world: dict | None = None) -> "Timeline":
        t = cls(pocket_name, world)
        for op in ops:
            kind = op["op"]
            if kind == "fork":
                t.fork(op["parent"], op["budget"])
            elif kind == "commit":
                t.commit(op["branch"], op["proposal"], source=op["source"], request_id=op["request_id"])
            elif kind == "author":
                t.author(op["branch"], op["delta"], op["summary"], request_id=op["request_id"],
                         actor=op.get("actor", "story"), target=op.get("target", ""))
            elif kind == "freeze":
                t.freeze(op["branch"])
            elif kind == "cross":
                t.cross(op["shadow"], op["origin"], request_id=op["request_id"])
            else:
                raise EngineError(f"unknown op {kind!r}")
        return t
