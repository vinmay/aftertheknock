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


def test_crossing_text_is_template_and_color_is_checked():
    """The inscription comes from the committed event via a hand-written template; a color
    sentence that breaks the rules (here: an invented number) is dropped, never shown."""
    bad = "It says 0000 in red."
    g = Game("bus ticket", 2, ScriptedModel([]), narrator_model=ScriptedModel([bad, bad]), background=False)
    g.choose(1)            # lock
    while g.scene != "crossing1":
        g.choose(0)
    cr = g.crossings["B"]
    assert cr["narration_source"] == "template"
    assert any(p.startswith("C_NUMBERS") for p in cr["raw"]["problems"])
    digits = g.timeline.event(cr["intrusion"]["origin_event_id"]).text
    assert digits in cr["narration"] and "0000" not in cr["narration"]
    assert "That wasn't there when you put it in your pocket." in cr["narration"]


def test_good_color_sentence_is_kept():
    good = "The radiator ticks twice, then goes silent."
    g = Game("bus ticket", 2, ScriptedModel([]), narrator_model=ScriptedModel([good]), background=False)
    g.choose(1)
    while g.scene != "crossing1":
        g.choose(0)
    assert g.crossings["B"]["narration_source"] == "template+model"
    assert g.crossings["B"]["narration"].endswith(good)


@pytest.mark.parametrize("text,ok", [
    ("The rain finds a gap in the window frame.", True),
    ("Help me, someone whispers.", False),
    ("You hear Mara breathing.", False),
    ("Cold. Then colder.", False),
    ("It reads 4815.", False),
])
def test_color_rules(text, ok):
    from knock.narrator import check
    assert (check(text) == []) is ok


def test_runs_vary_by_seed():
    chains = {tuple((e.actor, e.action, e.target, e.text) for e in play("lock", "roof", "open", RandomModel(), seed=s).timeline.branch("B").events)
              for s in range(1, 6)}
    assert len(chains) > 1


@pytest.mark.parametrize("raw,name,kind", [
    ("My lucky bus ticket", "lucky bus ticket", "object"),
    ("", "ticket stub", "empty"),
    (None, "ticket stub", "empty"),
    ("nothing", "ticket stub", "empty"),
    ("1234", "ticket stub", "empty"),
    ("regret", "ticket stub", "abstract"),
    ("a promise", "ticket stub", "abstract"),
    ("a receipt from 2019", "receipt", "object"),
    ("<script>alert(1)</script>", "ticket stub", "empty"),
    ("<b>receipt</b>", "receipt", "object"),
    ("a very long folded note from someone I used to know well", "very long folded note", "object"),
])
def test_pocket_inputs(raw, name, kind):
    from knock.game import pocket_info
    assert pocket_info(raw) == (name, kind)


@pytest.mark.parametrize("raw,line", [
    ("nothing", "You thought your pockets were empty. But your thumb catches a folded ticket stub. You don't remember keeping it."),
    ("regret", "You carry regret everywhere. Tonight your thumb also finds a folded ticket stub."),
    ("two pennies", "In your pocket, your thumb finds the two pennies. You've never been able to get rid of them."),
    ("my father's watch", "In your pocket, your thumb finds your father's watch. You've never been able to get rid of it."),
])
def test_pocket_line_in_opening(raw, line):
    g = Game(raw, 1, RandomModel(), background=False)
    assert line in g.view()["text"]


def test_plural_object_agreement():
    from knock.state import is_plural
    assert is_plural("two pennies") and is_plural("keys") and not is_plural("pair of gloves")
    assert not is_plural("glass") and not is_plural("bus ticket")
    g = play("lock", "roof", "open", RandomModel(), seed=6, pocket="two pennies")
    assert "when you put them in your pocket" in g.crossings["B"]["narration"]
    assert "Your two pennies are" in g.ending["text"]


@pytest.mark.parametrize("f1,f2,end", PATHS[::3])
def test_replay_reproduces_recorded_run(f1, f2, end):
    import json
    from knock.game import replay, verify_replay
    g = play(f1, f2, end, RandomModel(), seed=21)
    rec = json.loads(json.dumps(g.record()))
    g2 = replay(rec)
    assert verify_replay(g2, rec)
    assert g2.ending == g.ending
    assert [c["narration"] for c in g2.crossings.values()] == [c["narration"] for c in g.crossings.values()]


def test_replay_detects_tampering():
    import json
    from knock.game import replay, verify_replay
    g = play("lock", "roof", "erase", RandomModel(), seed=22)
    rec = json.loads(json.dumps(g.record()))
    for r in rec["log"]:
        if r["kind"] == "proposal" and r["decision"]["accepted"] and '"mark"' in r["raw"]:
            r["raw"] = r["raw"].replace(json.loads(r["raw"])["text"], "9999" if r["branch"] == "B" else "OTHER WORDS")
    assert not verify_replay(replay(rec), rec)


def test_every_shadow_ends_in_a_mark_across_many_seeds():
    """Random proposals over many seeds: both shadows end in a mark, on different objects."""
    from knock.sim import pick_origin
    for seed in range(40):
        f1, f2, end = PATHS[seed % len(PATHS)]
        g = play(f1, f2, end, RandomModel(), seed=seed)
        b, d = pick_origin(g.timeline, "B"), pick_origin(g.timeline, "D")
        assert b.action == d.action == "mark", (seed, f1, f2)
        assert b.target != d.target, (seed, f1, f2)


def test_crossing_memory_comes_from_committed_events():
    g = play("lock", "roof", "erase", ScriptedModel([]), seed=5)
    mem = g.trace("X1")["memory"]
    o = g.timeline.event(g.timeline.intrusions[0].origin_event_id)
    assert mem["memory"] == (f"In the night where you opened the door, Mara took the bus ticket and wrote "
                             f"\u201c{o.text}\u201d on it. This is where the numbers came from.")
    assert mem["source"].startswith("Written there: the night where you opened the door")
    assert mem["dest"].startswith("Appeared here: your night")


@pytest.mark.parametrize("f1,f2", [("lock", "basement"), ("lock", "roof"), ("open", "basement"), ("open", "roof")])
def test_second_crossing_lands_on_a_different_object(f1, f2):
    from knock.engine import crossed_objects
    g = play(f1, f2, "open", RandomModel(), seed=8)
    x1, x2 = g.timeline.intrusions
    o1, o2 = crossed_objects(x1.post_fork), crossed_objects(x2.post_fork)
    assert o1 and o2 and not (o1 & o2)
    # Preferred objects: lock path -> pocket first, key's tag second; open path -> the reverse.
    assert (o1, o2) == (({"pocket"}, {"key07"}) if f1 == "lock" else ({"key07"}, {"pocket"}))


@pytest.mark.parametrize("f1", ["lock", "open"])
def test_basement_digits_open_unit07(f1):
    g = Game("bus ticket", 4, RandomModel(), background=False)
    want = {"fork1": f1, "fork2": "basement", "final": "erase"}
    seen = []
    v = g.view()
    while v["choices"]:
        seen.append((g.scene, v))
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
    digits = g.timeline.event(g.crossings["B"]["intrusion"]["origin_event_id"]).text
    scenes = dict(seen)
    second = "lock_basement_2" if f1 == "lock" else "open_basement_2"
    assert second in scenes and scenes[second]["choices"] == [f"Dial {digits}"]
    assert digits in scenes["unit07_open"]["text"]
    if f1 == "lock":
        assert "A night," in scenes["lock_basement_2"]["text"]      # "what's in 07" gets an answer
    c = g.timeline.state("C")
    assert c["places"]["unit07"]["locked"] is False
    unlock = next(e for e in g.timeline.branch("C").events if ["set", "places.unit07.locked", False] in [list(d) for d in e.state_delta])
    assert unlock.evidence_refs == (g.crossings["B"]["intrusion"]["manifestation_event_id"],)
    assert digits in unlock.summary


@pytest.mark.parametrize("second,kept", [
    ("The rain ticks against the window, then goes silent.", False),       # the same images again
    ("Somewhere below, a pipe knocks once and settles.", True),
])
def test_second_color_sentence_never_repeats_the_first(second, kept):
    """The second crossing's prompt names the first sentence to avoid; a near-repeat is dropped
    without another model call."""
    first = "The rain ticks against the window, then goes quiet."
    g = Game("bus ticket", 5, ScriptedModel([]), narrator_model=ScriptedModel([first, second, "spare"]), background=False)
    want = {"fork1": "lock", "fork2": "roof", "final": "open"}
    v = g.view()
    while v["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
    assert g.crossings["B"]["parts"]["color"] == first
    assert first in g.crossings["D"]["prompt"][-1]["content"]
    assert first not in g.crossings["B"]["prompt"][-1]["content"]
    assert g.crossings["D"]["parts"]["color"] == (second if kept else None)
    calls = [r for r in g.log.records if r["kind"] == "narration" and r["branch"] == "C"]
    assert len(calls) == 1
    if not kept:
        assert calls[0]["problems"][0].startswith("C_REPEAT")
