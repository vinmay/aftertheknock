"""The rewritten story ("The other you"): every route reads cleanly, and the authored climax only ever
states the player's own choices, committed events of shadow B, or the March backstory."""

import copy
import dataclasses
import itertools
import re
from types import SimpleNamespace

import pytest

from knock.game import Game
from knock.models import RandomModel

PATHS = list(itertools.product(("lock", "open"), ("basement", "roof"), ("erase", "open")))
LONG = "a small folded letter from my grandmother I never once opened"
POCKETS = ["ticket stub", "my father's watch", "nothing", "a phone", "an old photograph", "house key", "regret", LONG]
FORBIDDEN = [
    re.compile(r"\b(key07|unit07|teo_apt|apt)\b"),
    re.compile(r"\b(objects|chars|places|events)\.\w"),
    re.compile(r"\bplayer\b", re.I),
    re.compile(r"\b(the|a|on|from|holds?|took|takes|wrote on) pocket\b", re.I),
    re.compile(r"\b[A-D]\.\d\d\b"),
    re.compile(r"\{\w+\}"),
]


def play(pocket, f1, f2, end, seed=7):
    g = Game(pocket, seed, RandomModel(), background=False)
    want = {"fork1": f1, "fork2": f2, "final": end}
    views = [g.view()]
    while views[-1]["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        views.append(g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0)))
    return g, views


def texts(views):
    out = []
    for v in views:
        out += [v["text"], *v["choices"]]
        if v.get("crossing"):
            out += [v["crossing"][k] for k in ("before", "after", "who", "echo") if v["crossing"].get(k)]
    return out


RUNS = [(p, *path) for p in POCKETS for path in PATHS]


@pytest.mark.parametrize("pocket,f1,f2,end", RUNS, ids=lambda x: str(x)[:14])
def test_every_route_reads_cleanly(pocket, f1, f2, end):
    g, views = play(pocket, f1, f2, end)
    scenes = [v["scene"] for v in views]
    assert scenes[-4:-1] == ["the_other_you", "confrontation", "final"] and scenes[-1] == f"ending_{end}"
    for t in texts(views):
        for pat in FORBIDDEN:
            m = pat.search(t)
            assert not m, (pocket, f1, f2, end, m.group(0), t[:120])
        assert not re.search(r"\b[Tt]he \w+'s watch", t), t       # possessives read "your father's watch"
    detail = g.other_you()["slots"]["other_detail"]
    if g.material in ("metal", "small"):
        assert "wrote" not in detail and "scratched" in detail or not detail or "Teo" in detail or "tag" in detail
    if g.material == "paper":
        assert "scratched" not in detail and "marked" not in detail


@pytest.mark.parametrize("pocket,f1,f2,end", RUNS[::3], ids=lambda x: str(x)[:14])
def test_the_other_you_states_only_committed_b_facts(pocket, f1, f2, end):
    g, views = play(pocket, f1, f2, end)
    conf = next(v for v in views if v["scene"] == "confrontation")["text"]
    other = g.other_you()
    b = {e.event_id: e for e in g.timeline.branch("B").events}
    assert set(other["claims"]) <= set(b), "every stated fact cites an event of shadow B"
    detail = g.fmt(other["slots"]["other_detail"]) if other["slots"]["other_detail"] else ""
    origin = g.timeline.event(g.crossings["B"]["intrusion"]["origin_event_id"])
    if detail:
        assert detail in conf and other["claims"]
    if "Teo opened his door" in detail:
        assert any(b[i].action == "open" and b[i].target == "teo_apt" and b[i].actor == "teo" for i in other["claims"])
    if "out of my hand" in detail:
        assert any(b[i].target == "pocket" and b[i].action in ("take", "give") for i in other["claims"])
        assert origin.actor == "mara"
    if origin.text in detail:
        assert origin.event_id in other["claims"]
    if f1 == "lock" and origin.actor == "teo":
        assert detail == ""                                   # no variant for Teo's hand: say nothing
    # The other you's own choices are the player's, inverted.
    assert ("I opened it." in conf) == (f1 == "lock") and ("I kept it locked." in conf) == (f1 == "open")
    assert ("One of us went up to the roof." in conf) == (f2 == "basement")


def _with_writer(g, actor, took):
    """Shadow B's mark as made by `actor`, with or without a committed take of the object by Mara."""
    oid = g.crossings["B"]["intrusion"]["origin_event_id"]
    real_event, real_branch = g.timeline.event, g.timeline.branch
    mark = dataclasses.replace(real_event(oid), actor=actor)
    events = [e for e in real_branch("B").events if not (e.target == "pocket" and e.action in ("take", "give"))]
    if took:
        events.append(dataclasses.replace(mark, event_id="B.99", action="take", actor="mara", text=""))
    g.timeline.event = lambda eid: mark if eid == oid else real_event(eid)
    g.timeline.branch = lambda bid: SimpleNamespace(events=events) if bid == "B" else real_branch(bid)


@pytest.mark.parametrize("pocket,verb", [("ticket stub", "wrote"), ("house key", "scratched"), ("a phone", "marked")])
@pytest.mark.parametrize("actor,took,expect", [
    ("mara", True, "out of my hand"), ("mara", False, "I watched her do it."), ("player", False, "myself"), ("teo", False, None)])
def test_other_detail_by_writer(pocket, verb, actor, took, expect):
    g, _ = play(pocket, "lock", "basement", "erase")
    if g.timeline.event(g.crossings["B"]["intrusion"]["origin_event_id"]).target != "pocket":
        pytest.skip("crossing 1 did not land on the pocket object in this run")
    _with_writer(g, actor, took)
    detail = g.other_you()["slots"]["other_detail"]
    if expect is None:
        assert detail == ""
        return
    assert expect in detail and verb in detail, detail
    assert ("B.99" in g.other_you()["claims"]) == took


def _patched_states(g, edit):
    real = g.timeline.state

    def state(bid):
        s = copy.deepcopy(real(bid))
        edit(bid, s)
        return s
    g.timeline.state = state


@pytest.mark.parametrize("status,line", [("present", "turns off his radio"), ("hurt", "This time you cross the hall"),
                                         ("dead", "nobody will knock for him")])
def test_teo_in_the_open_ending(status, line):
    g, _ = play("ticket stub", "open", "roof", "open")

    def edit(bid, s):
        if bid in ("B", "D"):
            s["chars"]["teo"]["status"] = status if bid == "D" else "present"
    _patched_states(g, edit)
    assert line in g.ending_text("open")


@pytest.mark.parametrize("split", [False, True])
def test_mara_in_the_open_ending(split):
    g, _ = play("ticket stub", "lock", "basement", "open")
    c, d = g.timeline.state("C"), g.timeline.state("D")
    mh = g._char_where(c, "mara")

    def wording(loc):                       # how Mara at `loc` in the other night would be described
        s = copy.deepcopy(d)
        s["chars"]["mara"]["location"] = loc
        return g._char_where(s, "mara")
    pick = next(loc for loc in d["places"] if (wording(loc) != mh) == split)

    def edit(bid, s):
        if bid == "D":
            s["chars"]["mara"]["location"] = pick
    _patched_states(g, edit)
    text = g.ending_text("open")
    assert ("She is also" in text) == split and ("She looks relieved" in text) == (not split)


@pytest.mark.parametrize("f1,f2,end", PATHS)
def test_no_shadow_fact_before_the_map_except_what_crossed(f1, f2, end):
    g, views = play("ticket stub", f1, f2, end)
    t = g.timeline
    b_text = t.event(g.crossings["B"]["intrusion"]["origin_event_id"]).text
    facts = {e.text for bid in ("B", "D") for e in t.branch(bid).events if e.text and len(e.text) >= 3}
    released = set()
    for v in views:
        if v["scene"] == "map":
            break
        if v["kind"] == "crossing":
            released.add(b_text if v["scene"] == "crossing1" else t.event(g.crossings["D"]["intrusion"]["origin_event_id"]).text)
            continue
        for s in [v["text"], *v["choices"]]:
            for f in facts - released:
                assert f not in s, (v["scene"], f)


@pytest.mark.parametrize("f1", ["lock", "open"])
def test_authored_scenes_add_no_events_and_are_labelled(f1):
    g = Game("ticket stub", 3, RandomModel(), background=False)
    want = {"fork1": f1, "fork2": "roof", "final": "erase"}
    v = g.view()
    counts = {}
    while v["choices"]:
        counts[g.scene] = sum(len(b.events) for b in g.timeline.branches.values())
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
        if g.scene in ("the_other_you", "confrontation"):
            assert v["authored"] == Game.AUTHORED
    assert counts["the_other_you"] == counts["confrontation"] == counts["final"] == counts["map"]
    assert [a["scene"] for a in g.authored_seen()] == ["the_other_you", "confrontation"]
    assert all(a["label"] == "Authored scene, built from your choices and the events shown on the map." for a in g.authored_seen())


@pytest.mark.parametrize("f1,line", [("lock", "You locked the door. Somewhere, you didn't."),
                                     ("open", "You opened the door. Somewhere, you didn't.")])
def test_first_crossing_echoes_the_first_choice(f1, line):
    g, views = play("ticket stub", f1, "basement", "open")
    assert g.crossings["B"]["parts"]["echo"] == line and line in g.crossings["B"]["narration"]
    assert "echo" not in g.crossings["D"]["parts"] and "Somewhere, you didn't" not in g.crossings["D"]["narration"]


@pytest.mark.parametrize("f1", ["lock", "open"])
def test_teo_on_the_roof_names_the_first_crossing_object(f1):
    g, views = play("ticket stub", f1, "roof", "erase")
    roof = next(v for v in views if v["scene"] == f"{f1}_roof")["text"]
    target = g.timeline.event(g.crossings["B"]["intrusion"]["origin_event_id"]).target
    name = {"pocket": "the ticket stub", "key07": "the key's paper tag"}[target]
    assert f"The numbers on {name}. That's the lock on 07." in roof
