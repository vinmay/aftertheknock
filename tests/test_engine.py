import dataclasses

import pytest

from knock.engine import (BranchRuleError, FrozenError, IntrusionError, RejectedError, Timeline)
from knock.state import state_hash


def forked(budget=3):
    t = Timeline("a bus ticket")
    t.author("S", [], "A knock at 11:47.", request_id="S:open")
    t.fork("S", budget=budget)
    t.author("A", [["set", "places.apt.locked", True]], "You lock the door.", request_id="A:lock")
    return t


def signature(t):
    t.author("B", [["set", "chars.mara.location", "apt"]], "Door opened.", request_id="B:setup")
    t.commit("B", {"actor": "mara", "action": "take", "target": "pocket"}, request_id="B:1")
    t.commit("B", {"actor": "mara", "action": "speak", "target": "player", "text": "Hold still."}, request_id="B:2")
    return t.commit("B", {"actor": "mara", "action": "mark", "target": "pocket", "text": "4815"}, request_id="B:3")


# ---- branching rule -----------------------------------------------------
def test_branching_rule_exact():
    t = forked()
    assert set(t.branches) == {"S", "A", "B"}
    assert t.branch("A").kind == "canonical" and t.branch("B").kind == "shadow"
    with pytest.raises(BranchRuleError):
        t.fork("S")                      # fork 1 happens once
    with pytest.raises(BranchRuleError):
        t.fork("B")                      # shadows never fork
    assert t.fork("A") == ("C", "D")
    with pytest.raises(BranchRuleError):
        t.fork("C")                      # no third fork
    with pytest.raises(BranchRuleError):
        t.fork("D")
    assert [b.id for b in t.branches.values() if b.kind == "shadow"] == ["B", "D"]


def test_shadow_freezes_at_budget():
    t = forked(budget=3)
    signature(t)
    assert t.branch("B").frozen
    with pytest.raises(RejectedError) as e:
        t.commit("B", {"actor": "mara", "action": "move", "target": "hall"}, request_id="B:4")
    assert e.value.decision.rules == ["R_FROZEN"]


# ---- shared history ------------------------------------------------------
def test_shared_history_is_immutable():
    t = forked()
    with pytest.raises(FrozenError):
        t.author("S", [["set", "chars.mara.location", "roof"]], "rewrite", request_id="S:x")
    with pytest.raises(RejectedError):
        t.commit("S", {"actor": "mara", "action": "move", "target": "stairwell"}, request_id="S:y")
    ev = t.branch("S").events[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.summary = "changed"
    assert t.lineage("A")[0] is ev and t.lineage("B")[0] is ev


# ---- idempotency ---------------------------------------------------------
def test_retried_proposal_applies_once():
    t = forked()
    p = {"actor": "mara", "action": "move", "target": "stairwell"}
    e1 = t.commit("B", p, request_id="B:1")
    e2 = t.commit("B", p, request_id="B:1")
    assert e1 is e2
    assert len(t.branch("B").events) == 1
    assert t.state("B")["chars"]["mara"]["location"] == "stairwell"
    a1 = t.author("A", [["append", "chars.player.said", "hello"]], "x", request_id="A:say")
    a2 = t.author("A", [["append", "chars.player.said", "hello"]], "x", request_id="A:say")
    assert a1 is a2 and t.state("A")["chars"]["player"]["said"] == ["hello"]


def test_rejected_proposal_leaves_state_untouched():
    t = forked()
    before = state_hash(t.state("B"))
    with pytest.raises(RejectedError):
        t.commit("B", {"actor": "mara", "action": "give", "target": "pocket", "to": "player"}, request_id="B:1")
    assert state_hash(t.state("B")) == before and t.branch("B").events == []


# ---- isolation -----------------------------------------------------------
def test_shadow_commits_never_touch_canonical():
    t = forked()
    a_before = state_hash(t.state("A"))
    signature(t)
    assert state_hash(t.state("A")) == a_before
    assert t.state("A")["objects"]["pocket"]["marks"] == []
    assert t.state("A")["objects"]["pocket"]["holder"] == "player"


def test_canonical_commits_never_touch_shadow():
    t = forked()
    b_before = state_hash(t.state("B"))
    t.commit("A", {"actor": "mara", "action": "move", "target": "stairwell"}, request_id="A:1")
    assert state_hash(t.state("B")) == b_before


def test_intrusion_cannot_be_written_directly_by_shadow():
    t = forked()
    from knock.engine import EngineError
    with pytest.raises(EngineError):
        t.commit("B", {"actor": "mara", "action": "move", "target": "stairwell"}, source="intrusion", request_id="B:1")


# ---- intrusions ----------------------------------------------------------
def test_signature_intrusion_carries_post_fork_fact():
    t = forked()
    mark = signature(t)
    x = t.cross("B", mark.event_id)
    assert x.post_fork == [["append", "objects.pocket.marks", "4815"]]
    assert t.state("A")["objects"]["pocket"]["marks"] == ["4815"]
    man = t.event(x.manifestation_event_id)
    assert man.origin == mark.event_id and man.branch == "A"


def test_intrusion_requires_frozen_shadow():
    t = forked(budget=4)
    mark = signature(t)
    with pytest.raises(IntrusionError):
        t.cross("B", mark.event_id)


def test_intrusion_must_reveal_new_fact():
    t = forked()
    t.author("B", [], "setup", request_id="B:setup")
    # Moving to the hall and back recreates no new fact relative to the fork.
    t.commit("B", {"actor": "mara", "action": "move", "target": "stairwell"}, request_id="B:1")
    back = t.commit("B", {"actor": "mara", "action": "move", "target": "hall"}, request_id="B:2")
    t.commit("B", {"actor": "teo", "action": "open", "target": "teo_apt"}, request_id="B:3")
    with pytest.raises(IntrusionError, match="no fact after the fork"):
        t.cross("B", back.event_id)


def test_intrusion_origin_must_be_committed_shadow_event():
    t = forked()
    signature(t)
    with pytest.raises(IntrusionError):
        t.cross("B", "B.99")
    with pytest.raises(IntrusionError):
        t.cross("B", "S.00")               # shared history is not a shadow event
    with pytest.raises(IntrusionError):
        t.cross("B", "B.01")               # the authored setup is not simulated


def test_exactly_one_intrusion_per_shadow():
    t = forked()
    mark = signature(t)
    t.cross("B", mark.event_id)
    assert t.cross("B", mark.event_id) is t.intrusions[0]   # same request: idempotent
    with pytest.raises(IntrusionError):
        t.cross("B", mark.event_id, request_id="again")


def test_provenance_invariant():
    t = forked()
    mark = signature(t)
    x = t.cross("B", mark.event_id)
    origin = t.event(x.origin_event_id)
    assert origin.branch == x.origin_branch == "B"
    allowed = [list(f) for f in origin.state_delta] + [["set", f"events.{origin.event_id}.actor", origin.actor]]
    assert all(f in allowed for f in x.released)
    assert t.released_facts("A") == x.released
    assert t.released_facts("B") == []


def test_mark_on_object_out_of_reach_arrives_with_player():
    t = Timeline("a bus ticket")
    t.author("S", [], "knock", request_id="S:open")
    t.fork("S", budget=3)
    t.author("A", [["set", "chars.mara.location", "stairwell"]], "Mara leaves.", request_id="A:1")
    t.author("B", [["set", "places.apt.locked", True]], "locked", request_id="B:setup")
    t.commit("B", {"actor": "teo", "action": "open", "target": "teo_apt"}, request_id="B:1")
    t.commit("B", {"actor": "mara", "action": "move", "target": "teo_apt"}, request_id="B:2")
    m = t.commit("B", {"actor": "mara", "action": "mark", "target": "key07", "text": "3147"}, request_id="B:3")
    t.cross("B", m.event_id)
    k = t.state("A")["objects"]["key07"]
    assert k["marks"] == ["3147"] and k["holder"] is None and k["location"] == "apt"


# ---- replay --------------------------------------------------------------
def test_replay_reproduces_state():
    t = forked()
    mark = signature(t)
    t.cross("B", mark.event_id)
    t.fork("A")
    t2 = Timeline.replay(t.ops, t.pocket_name)
    assert t2.hashes() == t.hashes()
    assert [e.to_dict() for e in t2.lineage("C")] == [e.to_dict() for e in t.lineage("C")]
    for bid in t.branches:
        assert t.replay_state(bid) == t.state(bid)


def test_later_crossing_must_land_on_a_different_object():
    t = forked()
    mark = signature(t)                       # crossing 1 marks the pocket object
    t.cross("B", mark.event_id)
    t.fork("A", budget=1)
    t.author("D", [], "setup", request_id="D:setup")
    again = t.commit("D", {"actor": "player", "action": "mark", "target": "pocket", "text": "AGAIN"}, request_id="D:1")
    with pytest.raises(IntrusionError, match="different object"):
        t.cross("D", again.event_id)


def test_only_marks_manifest():
    """Speech in a shadow can cross only as a revealed fact; it never changes canonical state."""
    t = forked()
    t.cross("B", signature(t).event_id)
    c = t.state("A")
    assert "heard" not in c["chars"]["player"]
    assert c["chars"]["mara"]["said"] == []
