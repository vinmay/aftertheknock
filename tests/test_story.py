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
    assert scenes[-5:-1] == ["crossing2", "the_other_you", "confrontation", "final"] and scenes[-1] == f"ending_{end}"
    assert "map" not in scenes                                   # the map opens only from the ending
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
    # The other you's own choices are the player's, inverted; the door appears where the player is.
    assert ("I opened it. I let her in." in conf) == (f1 == "lock") and ("I kept it locked." in conf) == (f1 == "open")
    assert ("You're the one who changed." in conf) == (f1 == "open")
    door = next(v for v in views if v["scene"] == "the_other_you")["text"]
    assert ("row of cages" in door) == (f2 == "basement") and ("stairwell door any more" in door) == (f2 == "roof")
    # Mara witnesses only when her committed position in this night is beside the player.
    c = g.timeline.state("C")["chars"]
    assert ("Mara has gone very still" in conf) == (c["mara"]["location"] == c["player"]["location"])
    for old in ("I remember standing where you stood", "One of us went", "You should have done what I did"):
        assert old not in conf


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


TEO = {"present": "Teo's chain slides back", "hurt": "you both turn toward the stairs", "dead": "it's still too late"}
ECHO = {("erase", "roof"): "Teo heard you lock it.", ("erase", "basement"): "You saw. You locked it anyway.",
        ("open", "roof"): "Teo was right. Up was just weather.", ("open", "basement"): "She has her witness now."}
HOME_ONLY = re.compile(r"across the hall|narrow entry|\bentry\b|your table|your kitchen|the fridge begins", re.I)


@pytest.mark.parametrize("f1,f2", [(a, b) for a in ("lock", "open") for b in ("basement", "roof")])
@pytest.mark.parametrize("status", list(TEO))
@pytest.mark.parametrize("beside", [True, False])
def test_routes_by_teo_and_mara(f1, f2, status, beside):
    """Every route x both endings x Teo fine/hurt/dead x Mara beside you or not."""
    """Every route x Teo fine/hurt/dead x Mara beside you or not: no unfilled slot, the right Teo line in
    the open ending, and Mara's witness line exactly when she is beside the player in this night."""
    g, views = play("ticket stub", f1, f2, "open")
    real = g.timeline.state

    def state(bid):
        s = copy.deepcopy(real(bid))
        if bid in ("B", "D"):
            s["chars"]["teo"]["status"] = status if bid == "D" else "present"
        if bid == "C":
            here = s["chars"]["player"]["location"]
            if beside:
                s["chars"]["mara"]["location"] = here
            elif s["chars"]["mara"]["location"] == here:
                s["chars"]["mara"]["location"] = next(p for p in s["places"] if p != here)
        return s
    g.timeline.state = state
    conf, ending, erased = g.fmt(g.story["scenes"]["confrontation"]["text"]), g.ending_text("open"), g.ending_text("erase")
    for t in (conf, ending, erased):
        assert not re.search(r"\{\w+\}", t), t
        assert not HOME_ONLY.search(t), HOME_ONLY.search(t).group(0)     # the ending is on the roof or in the basement
    assert ("Mara has gone very still" in conf) == beside
    assert TEO[status] in ending
    assert sum(line in ending for line in TEO.values()) == 1
    # The second choice echoes in both endings; Mara's witness line only when she is beside you.
    assert ECHO[("erase", f2)] in erased
    assert (ECHO[("open", f2)] in ending) == (f2 == "roof" or beside)
    other = "basement" if f2 == "roof" else "roof"
    assert ECHO[("erase", other)] not in erased and ECHO[("open", other)] not in ending


@pytest.mark.parametrize("split", [False, True])
def test_mara_in_what_changed(split):
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
    changed = " ".join(g.changed)
    assert ("She is also" in changed) == split and ("She looks relieved" in changed) == (not split)
    assert "She is also" not in text and "She looks relieved" not in text     # positions live in the evidence view


@pytest.mark.parametrize("f1,f2", [("lock", "basement"), ("open", "roof")])
def test_opening_flashbacks_never_changes_the_story(f1, f2):
    """Reading each crossing's trace (the flashback) or skipping it produces exactly the same text."""
    def run(read):
        g = Game("ticket stub", 9, RandomModel(), background=False)
        want = {"fork1": f1, "fork2": f2, "final": "open"}
        v, out = g.view(), []
        while v["choices"]:
            out.append((v["scene"], v["text"], tuple(v["choices"])))
            if read and v.get("trace"):
                g.trace(v["trace"]["intrusion"]["intrusion_id"])
            sc = g.story["scenes"][g.scene]
            w = want.get(sc.get("kind"))
            v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
        return out + [(v["scene"], v["text"])]
    assert run(True) == run(False)


@pytest.mark.parametrize("f1,f2,end", PATHS)
def test_no_shadow_fact_before_the_door_except_what_crossed(f1, f2, end):
    g, views = play("ticket stub", f1, f2, end)
    t = g.timeline
    b_text = t.event(g.crossings["B"]["intrusion"]["origin_event_id"]).text
    facts = {e.text for bid in ("B", "D") for e in t.branch(bid).events if e.text and len(e.text) >= 3}
    released = set()
    for v in views:
        if v["scene"] == "the_other_you":
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
    assert counts["the_other_you"] == counts["confrontation"] == counts["final"] == counts["crossing2"]
    assert [a["scene"] for a in g.authored_seen()] == ["the_other_you", "confrontation"]
    assert all(a["label"] == "Authored scene, built from your choices and the logged events of the other night."
               for a in g.authored_seen())


@pytest.mark.parametrize("f1,line", [("lock", "You locked the door. Somewhere, you didn't."),
                                     ("open", "You opened the door. Somewhere, you didn't.")])
def test_first_crossing_echoes_the_first_choice(f1, line):
    g, views = play("ticket stub", f1, "basement", "open")
    assert g.crossings["B"]["parts"]["echo"] == line and line in g.crossings["B"]["narration"]
    assert "Somewhere, you didn't" not in g.crossings["D"]["narration"]


@pytest.mark.parametrize("f1", ["lock", "open"])
def test_teo_confesses_on_the_roof(f1):
    g, views = play("ticket stub", f1, "roof", "erase")
    roof = next(v for v in views if v["scene"] == f"{f1}_roof")["text"]
    assert '"You came up," he calls. "Nobody ever does what I say."' in roof
    assert "I was sitting on the bed, and I was in the kitchen, talking." in roof
    assert '"Four numbers. I heard her say them tonight, through the wall. In the other night. They open 07. Don\'t go down there."' in roof
    assert roof.rstrip().endswith("though nobody came through it.")
    scenes = [v["scene"] for v in views]
    assert scenes.index("crossing1") < scenes.index(f"{f1}_roof")          # the numbers line comes after crossing 1


@pytest.mark.parametrize("f1,f2", [(a, b) for a in ("lock", "open") for b in ("basement", "roof")])
def test_crossing_two_is_in_your_own_hand_and_echoes_fork_two(f1, f2):
    g, views = play("ticket stub", f1, f2, "open")
    d = g.timeline.event(g.crossings["D"]["intrusion"]["origin_event_id"])
    b = g.timeline.event(g.crossings["B"]["intrusion"]["origin_event_id"])
    assert d.actor == "player" and d.target != b.target                     # your hand; a different object
    line = {"roof": "You trusted Teo. Somewhere, you followed her down.",
            "basement": "You followed Mara. Somewhere, you trusted Teo."}[f2]
    assert g.crossings["D"]["parts"]["echo"] == line and line in g.crossings["D"]["narration"]
    if f1 == "lock":                                                         # Mara handed you the key first
        assert any(e.action == "give" and e.target == "key07" and e.to == "player" for e in g.timeline.branch("D").events)


@pytest.mark.parametrize("f1,f2", [(a, b) for a in ("lock", "open") for b in ("basement", "roof")])
def test_crossing_lines_end_with_your_own_choice(f1, f2):
    g, views = play("ticket stub", f1, f2, "open")
    for shadow in ("B", "D"):
        paras = g.crossings[shadow]["narration"].split("\n\n")
        assert paras[-1] == g.crossings[shadow]["parts"]["echo"], paras
        if g.crossings[shadow]["parts"]["color"]:
            assert paras[-2] == g.crossings[shadow]["parts"]["color"]


@pytest.mark.parametrize("f1,f2", [(a, b) for a in ("lock", "open") for b in ("basement", "roof")])
def test_whose_handwriting_only_when_you_wrote_it(f1, f2):
    g, _ = play("ticket stub", f1, f2, "open")
    line = "One of them is in your handwriting. Now you know whose."
    assert line in g.ending_text("open") and "You understand now who it was meant for." not in g.ending_text("open")
    oid = g.crossings["D"]["intrusion"]["origin_event_id"]
    real = g.timeline.event
    g.timeline.event = lambda eid: dataclasses.replace(real(eid), actor="mara") if eid == oid else real(eid)
    assert line not in g.ending_text("open")


def test_script_lines():
    g, views = play("ticket stub", "lock", "basement", "erase")
    b2 = next(v for v in views if v["scene"] == "lock_basement_2")["text"]
    paras = b2.split("\n\n")
    assert paras[0].startswith('"A night," she says.') and paras[1].startswith('"I need someone else to see it,"')
    erased = g.ending_text("erase")
    assert "and down in the dark of it a fridge begins to hum as if nothing happened." in erased
    assert g.story["endings"]["slots"]["erase_mara_absent"] == "Far below, a metal door closes, gently, and does not open again."
    assert g.story["endings"]["slots"]["other_detail_took"].endswith("I didn't stop her.\"")


def test_a_place_you_own_takes_your_pronoun():
    from knock.describe import describe
    g, _ = play("ticket stub", "open", "roof", "erase")         # shadow B: the door stayed locked; Teo opens 6D
    teo = next(e for e in g.timeline.branch("B").events if e.action == "open" and e.target == "teo_apt")
    m = g.map_data()
    desc = next(x["desc"] for x in m["events"] if x["event_id"] == teo.event_id)
    assert desc == "Teo unlocked his apartment, 6D.", desc
    x = next(x for x in g.timeline.intrusions if x.origin_branch == "B")
    lines = [l["line"] for l in g.memory(x.intrusion_id)["scene"]]
    assert any("his apartment, 6D" in l for l in lines) and not any("Teo unlocks Teo's" in l for l in lines), lines
