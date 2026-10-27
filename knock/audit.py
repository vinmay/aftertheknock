"""Invariant checks over a finished run. Used by tests, the kill test and the eval."""

from __future__ import annotations

from .engine import SIBLING, Timeline
from .state import fact_holds


def shadow_tokens(t: Timeline) -> dict[str, set[str]]:
    """Text created inside each shadow (marks and speech)."""
    out = {}
    for bid in ("B", "D"):
        if bid in t.branches:
            out[bid] = {e.text for e in t.branch(bid).events if e.text}
    return out


def isolation(t: Timeline) -> list[str]:
    """Unintended state crossings. Target: none."""
    problems = []
    # Each branch's lineage contains only its own events and its ancestors' shared prefix.
    for bid, b in t.branches.items():
        allowed = {bid}
        p = b.parent
        while p:
            allowed.add(p)
            p = t.branch(p).parent
        for e in t.lineage(bid):
            if e.branch not in allowed:
                problems.append(f"{bid} lineage contains {e.event_id} from {e.branch}")
        if t.replay_state(bid) != t.state(bid):
            problems.append(f"{bid} state differs from replay of its lineage")
    # Canonical timelines never contain simulated events; only story and intrusion events.
    for bid in ("A", "C"):
        if bid in t.branches:
            for e in t.branch(bid).events:
                if e.source not in ("story", "intrusion"):
                    problems.append(f"{e.event_id} in canonical {bid} has source {e.source}")
    # Any shadow-created text present in canonical state arrived through an intrusion.
    tokens = shadow_tokens(t)
    for bid in ("A", "C"):
        if bid not in t.branches:
            continue
        s = t.state(bid)
        via_intrusion = {f[2] for f in t.released_facts(bid)}
        for oid, o in s["objects"].items():
            for mark in o["marks"]:
                if any(mark in toks for toks in tokens.values()) and mark not in via_intrusion:
                    problems.append(f"{bid}: mark {mark!r} on {oid} has no intrusion")
        for heard in s["chars"]["player"]["heard"]:
            if heard not in via_intrusion:
                problems.append(f"{bid}: player heard {heard!r} without an intrusion")
        for cid, c in s["chars"].items():
            for said in c["said"]:
                if any(said in toks for toks in tokens.values()):
                    problems.append(f"{bid}: {cid} says shadow speech {said!r}")
    return problems


def post_fork(t: Timeline) -> list[tuple[str, bool]]:
    """For each intrusion: does it reveal at least one fact created after its shadow's fork?"""
    out = []
    for x in t.intrusions:
        shadow = t.branch(x.origin_branch)
        ok = bool(x.post_fork) and all(not fact_holds(shadow.base_state, *f) for f in x.post_fork)
        out.append((x.intrusion_id, ok))
    return out


def provenance(t: Timeline) -> list[tuple[str, list[str]]]:
    """For each intrusion: problems with its origin. An empty list means valid."""
    out = []
    for x in t.intrusions:
        problems = []
        try:
            origin = t.event(x.origin_event_id)
        except Exception:
            out.append((x.intrusion_id, ["origin event does not exist"]))
            continue
        if origin.branch != x.origin_branch or t.branch(origin.branch).kind != "shadow":
            problems.append("origin is not in a shadow")
        if origin not in t.branch(x.origin_branch).events:
            problems.append("origin is not a committed event of the shadow")
        if origin.source not in ("model", "fallback"):
            problems.append("origin is not a simulated event")
        if SIBLING.get(x.origin_branch) != x.target_branch:
            problems.append("intrusion target is not the shadow's sibling")
        allowed = [list(f) for f in origin.state_delta] + [["set", f"events.{origin.event_id}.actor", origin.actor]]
        if any(list(f) not in allowed for f in x.released):
            problems.append("released facts not permitted by origin event")
        man = t.event(x.manifestation_event_id)
        if man.origin != origin.event_id or man.branch != x.target_branch:
            problems.append("manifestation does not cite its origin")
        out.append((x.intrusion_id, problems))
    return out


def narrator_blind(t: Timeline, prompts: list[tuple[list[dict], list]]) -> list[str]:
    """Each narrator prompt may contain shadow text only if an intrusion had released it
    by the time the prompt was built. `prompts` is [(messages, released_facts_at_that_time)]."""
    problems = []
    tokens = shadow_tokens(t)
    for msgs, released_then in prompts:
        released = {f[2] for f in released_then if isinstance(f[2], str)}
        blob = "\n".join(m["content"] for m in msgs)
        for bid, toks in tokens.items():
            for tok in toks:
                if tok in blob and tok not in released:
                    problems.append(f"narrator prompt contains unreleased {bid} text {tok!r}")
    return problems


def replays(t: Timeline) -> bool:
    t2 = Timeline.replay(t.ops, t.pocket_name)
    return t2.hashes() == t.hashes()
