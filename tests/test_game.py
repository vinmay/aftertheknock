import itertools

import pytest

from knock import audit
from knock.game import Game, sanitize_pocket
from knock.models import RandomModel, ScriptedModel

PATHS = list(itertools.product(("lock", "open"), ("basement", "roof"), ("erase", "open")))


def play(f1, f2, end, model, seed=1, pocket="bus ticket"):
    g = Game(pocket, seed, model, background=False)
    want = {"fork1": f1, "fork2": f2, "final": end}
    v = g.view()
    while v["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        idx = next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0)
        v = g.choose(idx)
    return g


@pytest.mark.parametrize("f1,f2,end", PATHS)
def test_every_path_completes_with_invariants(f1, f2, end):
    g = play(f1, f2, end, RandomModel(), seed=11)
    t = g.timeline
    assert g.view()["kind"] == "ending"
    assert set(t.branches) == {"S", "A", "B", "C", "D"}
    assert len(t.intrusions) == 2
    assert audit.isolation(t) == []
    assert all(ok for _, ok in audit.post_fork(t))
    assert all(not p for _, p in audit.provenance(t))
    assert audit.replays(t)
    prompts = [(c["prompt"], c["released_at"]) for c in g.crossings.values()]
    assert audit.narrator_blind(t, prompts) == []
    text = g.view()["text"]
    assert "{" not in text and "}" not in text
    for x in t.intrusions:
        assert t.event(x.origin_event_id).text in text


def test_signature_moment_lock_path():
    g = play("lock", "basement", "erase", ScriptedModel([]), seed=5)
    x = g.timeline.intrusions[0]
    origin = g.timeline.event(x.origin_event_id)
    assert origin.branch == "B" and origin.action == "mark" and origin.target == "pocket"
    assert origin.text in g.timeline.state("A")["objects"]["pocket"]["marks"]
    assert origin.text in g.crossings["B"]["narration"]
    tr = g.trace(x.intrusion_id)
    assert tr["origin"]["event_id"] == origin.event_id


def test_narrator_never_sees_shadow_before_release():
    """Canonical narrator prompts at crossing 1 contain nothing from D and no shadow speech."""
    g = play("open", "roof", "open", RandomModel(), seed=4)
    first_prompt = "\n".join(m["content"] for m in g.crossings["B"]["prompt"])
    for e in g.timeline.branch("D").events:
        if e.text:
            assert e.text not in first_prompt
    for e in g.timeline.branch("B").events:
        if e.action == "speak":
            assert e.text not in first_prompt


def test_narration_falls_back_when_model_contradicts():
    # The B shadow gets no usable proposals; the narrator invents a different number.
    bad = "You look at the ticket. It says 0000 in red."
    g = Game("bus ticket", 2, ScriptedModel([]), narrator_model=ScriptedModel([bad, bad]), background=False)
    g.choose(1)            # lock
    while g.scene != "crossing1":
        g.choose(0)
    cr = g.crossings["B"]
    assert cr["narration_source"] == "fallback"
    assert any(p.startswith("C_TEXT") for p in cr["raw"]["problems"])
    digits = g.timeline.event(cr["intrusion"]["origin_event_id"]).text
    assert digits in cr["narration"]


def test_runs_vary_by_seed():
    chains = {tuple((e.actor, e.action, e.target, e.text) for e in play("lock", "roof", "open", RandomModel(), seed=s).timeline.branch("B").events)
              for s in range(1, 6)}
    assert len(chains) > 1


@pytest.mark.parametrize("raw,expected", [
    ("My lucky bus ticket", "lucky bus ticket"),
    ("", "ticket stub"),
    (None, "ticket stub"),
    ("nothing", "ticket stub"),
    ("1234", "ticket stub"),
    ("a receipt from 2019", "receipt from"),
    ("<script>alert(1)</script>", "script alert script"),
    ("an   old   brass   button   from   my   coat", "old brass button from my"),
])
def test_pocket_sanitized(raw, expected):
    assert sanitize_pocket(raw) == expected
