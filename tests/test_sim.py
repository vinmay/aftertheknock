from knock.engine import Timeline
from knock.models import RandomModel, ScriptedModel
from knock.sim import RunLog, load_shadows, pick_origin, simulate_shadow, trivial_code


def forked():
    t = Timeline("a bus ticket")
    t.author("S", [], "A knock at 11:47.", request_id="S:open")
    t.fork("S", budget=4)
    return t


def test_impossible_give_is_rejected_then_fallback_commits():
    # The model insists Mara gives away the pocket object she does not hold.
    bad = {"summary": "Mara hands over the ticket.", "actor": "mara", "action": "give",
           "target": "pocket", "recipient": "player", "text": ""}
    good_speak = {"summary": "Mara speaks.", "actor": "mara", "action": "speak", "target": "player",
                  "recipient": "", "text": "Hello again."}
    model = ScriptedModel([good_speak, bad, bad, bad,
                           {"summary": "", "actor": "mara", "action": "mark", "target": "pocket",
                            "recipient": "", "text": "4815"}])
    t = forked()
    log = RunLog(1, {})
    simulate_shadow(t, "B", "B.open", model, 1, log)
    rejected = [r for r in log.records if r["kind"] == "proposal" and not r["decision"]["accepted"]]
    assert len(rejected) == 3
    assert all("R_HOLDS" in [v["rule"] for v in r["decision"]["violations"]] for r in rejected)
    # Feedback reaches the model on retry.
    assert "R_HOLDS" in model.calls[2][-1]["content"]
    fb = [r for r in log.records if r["kind"] == "fallback"]
    assert len(fb) == 1 and fb[0]["step"] == 1
    assert t.state("B")["objects"]["pocket"]["holder"] == "mara"
    origin = pick_origin(t, "B")
    assert origin.action == "mark" and origin.text == "4815" and origin.source == "model"


def test_all_failures_still_produce_signature_mark():
    t = forked()
    log = RunLog(7, {})
    simulate_shadow(t, "B", "B.open", ScriptedModel(["not json"] * 20), 7, log)
    assert t.branch("B").frozen
    origin = pick_origin(t, "B")
    assert origin.action == "mark" and origin.target == "pocket" and origin.source == "fallback"
    assert len(origin.text) == 4 and origin.text.isdigit() and not trivial_code(origin.text)


def test_every_spec_completes_with_fallbacks_only():
    """With a model that never produces anything usable, every shadow still ends in a mark."""
    from tests.test_game import play
    for f1 in ("lock", "open"):
        for f2 in ("basement", "roof"):
            g = play(f1, f2, "open", ScriptedModel([]), seed=3)
            for shadow, action in (("B", "mark"), ("D", "speak")):
                o = pick_origin(g.timeline, shadow, action)
                assert o.action == action and o.source == "fallback", (f1, f2, shadow)


def test_random_model_runs_vary_by_seed():
    chains = set()
    for seed in range(1, 6):
        t = forked()
        simulate_shadow(t, "B", "B.open", RandomModel(), seed, RunLog(seed, {}))
        chains.add(tuple((e.actor, e.action, e.target, e.text) for e in t.branch("B").events))
    assert len(chains) > 1


def test_specs_load():
    specs = load_shadows()
    assert {"B.open", "B.lock", "D.lock.roof", "D.lock.basement", "D.open.roof", "D.open.basement"} <= set(specs)
    for k, v in specs.items():
        assert 2 <= len(v["steps"]) <= 4, k
        last = v["steps"][-1]
        assert last.get("mark") if k.startswith("B") else last.get("speak"), k


def test_banned_phrases_are_rejected():
    """A proposal using a cliche is rejected even when the world allows it."""
    cliche = {"summary": "Mara says it.", "actor": "mara", "action": "speak", "target": "player",
              "recipient": "", "text": "Help me, please."}
    fine = {"summary": "Mara says it.", "actor": "mara", "action": "speak", "target": "player",
            "recipient": "", "text": "You kept the receipt."}
    t = forked()
    log = RunLog(1, {})
    simulate_shadow(t, "B", "B.open", ScriptedModel([cliche, fine, "{}", "{}", "{}", "{}", "{}", "{}"]), 1, log)
    first, second = [r for r in log.records if r["kind"] == "proposal"][:2]
    assert not first["decision"]["accepted"]
    assert any("banned phrase" in v["detail"] for v in first["decision"]["violations"])
    assert second["decision"]["accepted"]


def test_banned_phrase_matching():
    from knock.narrator import banned_in
    assert banned_in("HELP ME") == ["help me"]
    assert banned_in("You\u2019re next.") == ["you're next"]
    assert banned_in("It's not over yet") == ["it's not over"]
    assert banned_in("Helpme") == [] and banned_in("whelp merely") == []
