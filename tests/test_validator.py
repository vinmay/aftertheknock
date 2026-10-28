"""One test (at least) per validator rule."""

import pytest

from knock.state import apply_delta, initial_state
from knock.validator import validate


@pytest.fixture
def s():
    return initial_state(pocket_name="a bus ticket")


def rules(state, **p):
    d = validate(state, p)
    return d.rules


def test_actor_and_target_must_exist(s):
    assert rules(s, actor="ghost", action="move", target="hall") == ["R_EXISTS"]
    assert rules(s, actor="mara", action="take", target="umbrella") == ["R_EXISTS"]
    assert "R_EXISTS" in rules(s, actor="mara", action="give", target="key07", to="nobody")


def test_unknown_action_and_missing_fields(s):
    assert rules(s, actor="mara", action="teleport", target="roof") == ["R_SCHEMA"]
    assert rules(s, actor="mara", action="move") == ["R_SCHEMA"]


def test_cannot_give_unheld_object(s):
    # The deliberate impossible action: Mara gives away the pocket object she does not hold.
    s = apply_delta(s, [["set", "chars.mara.location", "apt"]])
    assert rules(s, actor="mara", action="give", target="pocket", to="player") == ["R_HOLDS"]


def test_cannot_mark_or_hide_unheld_object(s):
    assert rules(s, actor="player", action="mark", target="key07", text="1") == ["R_HOLDS"]
    assert rules(s, actor="player", action="hide", target="key07") == ["R_HOLDS"]


def test_give_requires_recipient_in_same_place(s):
    assert rules(s, actor="mara", action="give", target="key07", to="teo") == ["R_REACH"]
    s2 = apply_delta(s, [["set", "chars.mara.location", "apt"]])
    d = validate(s2, {"actor": "mara", "action": "give", "target": "key07", "to": "player"})
    assert d.accepted and d.delta == [["set", "objects.key07.holder", "player"]]


def test_take_requires_reach(s):
    assert rules(s, actor="mara", action="take", target="pocket") == ["R_REACH"]


def test_move_must_be_adjacent(s):
    assert rules(s, actor="mara", action="move", target="roof") == ["R_ADJACENT"]
    assert validate(s, {"actor": "mara", "action": "move", "target": "stairwell"}).accepted


def test_move_to_current_place_is_noop(s):
    assert rules(s, actor="mara", action="move", target="hall") == ["R_NOOP"]


def test_locked_place_needs_key(s):
    assert rules(s, actor="mara", action="move", target="teo_apt") == ["R_LOCKED"]
    s2 = apply_delta(s, [["set", "chars.mara.location", "basement"]])
    d = validate(s2, {"actor": "mara", "action": "move", "target": "unit07"})
    assert d.accepted
    assert ["set", "places.unit07.locked", False] in d.delta


def test_open_from_inside_needs_no_key(s):
    assert validate(s, {"actor": "teo", "action": "open", "target": "teo_apt"}).accepted
    assert rules(s, actor="mara", action="open", target="teo_apt") == ["R_LOCKED"]


def test_one_location_per_character(s):
    d = validate(s, {"actor": "mara", "action": "move", "target": "stairwell"})
    after = apply_delta(s, d.delta)
    assert after["chars"]["mara"]["location"] == "stairwell"
    # Exactly one location field changed, and it holds a single place.
    assert [x for x in d.delta if x[1].startswith("chars.mara")] == [["set", "chars.mara.location", "stairwell"]]


def test_dead_or_departed_take_no_actions(s):
    for status in ("dead", "departed"):
        s2 = apply_delta(s, [["set", "chars.teo.status", status]])
        assert rules(s2, actor="teo", action="open", target="teo_apt") == ["R_ACTIVE"]


def test_cannot_act_on_inactive_character(s):
    s2 = apply_delta(s, [["set", "chars.teo.status", "departed"], ["set", "chars.teo.location", "hall"]])
    assert rules(s2, actor="mara", action="speak", target="teo", text="hi") == ["R_TARGET_ACTIVE"]


def test_type_mismatch(s):
    assert rules(s, actor="mara", action="move", target="key07") == ["R_TYPE"]
    assert rules(s, actor="mara", action="harm", target="key07") == ["R_TYPE"]


def test_mark_needs_text(s):
    assert rules(s, actor="player", action="mark", target="pocket", text="") == ["R_TEXT"]
    assert rules(s, actor="player", action="mark", target="pocket", text="x" * 30) == ["R_TEXT"]


def test_cannot_harm_self(s):
    assert "R_SELF" in rules(s, actor="mara", action="harm", target="mara")


def test_harm_escalates(s):
    s = apply_delta(s, [["set", "chars.teo.location", "hall"]])
    d1 = validate(s, {"actor": "mara", "action": "harm", "target": "teo"})
    s = apply_delta(s, d1.delta)
    assert s["chars"]["teo"]["status"] == "hurt"
    s = apply_delta(s, validate(s, {"actor": "mara", "action": "harm", "target": "teo"}).delta)
    assert s["chars"]["teo"]["status"] == "dead"


def test_hidden_object_must_be_discovered_before_taking(s):
    s = apply_delta(s, [["set", "chars.mara.location", "apt"]])
    s = apply_delta(s, validate(s, {"actor": "player", "action": "hide", "target": "pocket"}).delta)
    assert rules(s, actor="mara", action="take", target="pocket") == ["R_HIDDEN"]
    s = apply_delta(s, validate(s, {"actor": "mara", "action": "discover", "target": "pocket"}).delta)
    assert validate(s, {"actor": "mara", "action": "take", "target": "pocket"}).accepted


def test_frozen_branch_rejects_everything(s):
    d = validate(s, {"actor": "mara", "action": "move", "target": "stairwell"}, frozen=True)
    assert d.rules == ["R_FROZEN"]


def test_speak_aloud_only_where_you_are(s):
    assert validate(s, {"actor": "mara", "action": "speak", "target": "hall", "text": "Hello."}).accepted
    d = validate(s, {"actor": "mara", "action": "speak", "target": "roof", "text": "Hello."})
    assert d.rules == ["R_REACH"] and "to speak aloud, the target is hall" in d.violations[0][1]


@pytest.mark.parametrize("text", ["…something else I don", "I kept the key for you, and something else I don",
                                  "The damp air carries things you shouldn't look for tonight a"])
def test_truncated_speech_is_rejected(s, text):
    from knock.validator import looks_truncated
    assert looks_truncated(text)
    d = validate(s, {"actor": "mara", "action": "speak", "target": "hall", "text": text})
    assert d.rules == ["R_TEXT"] and "Finish the sentence in fewer words." in d.violations[0][1]


@pytest.mark.parametrize("text", ["I kept your place.", "Leave the light off", "Count the stairs on the way down.",
                                  "“You already let me in.”", "Wait.", "Leave it",
                                  "I'll find you"])
def test_finished_speech_is_accepted(s, text):
    assert validate(s, {"actor": "mara", "action": "speak", "target": "hall", "text": text}).accepted


def test_truncated_committed_speech_is_shown_with_ellipsis_but_proof_is_exact():
    from knock.describe import display_speech
    raw = "…something else I don"
    assert display_speech(raw) == raw + "…"
    assert display_speech("I kept your place.") == "I kept your place."


@pytest.mark.parametrize("text", ["FLOOR COOKIE C", "BASSMENT STATIC", "R O O F B  A S E M E N", "FLOOR   ANTICAMBER  B",
                                  "SIXTH FLOOR  LLB", "FIVELIGHT DOOR", "BATHROOM A", "COUNT THE",
                                  "FIND THE COLD SPOT", "COUNT THE STAIRS", "SIXTH FLOOR DOOR", "STAIRWELL", "ROOF"])
def test_word_salad_marks_are_rejected(text):
    from knock.sim import words_problem
    assert words_problem(text)


@pytest.mark.parametrize("text", ["FIND THE KEY", "YOU WERE RIGHT", "WAIT FOR ME", "I STAYED", "ASK FOR ELENA",
                                  "DON'T OPEN SEVEN"])
def test_readable_marks_are_accepted(text):
    from knock.sim import FALLBACK_WORDS, words_problem
    assert words_problem(text) is None
    assert all(words_problem(w) is None for w in FALLBACK_WORDS)


def test_words_mark_step_rejects_word_salad_with_a_reason():
    from knock.sim import beat_violations
    step = {"mark": "words"}
    v = beat_violations(step, {"actor": "mara", "action": "mark", "target": "key07", "text": "FLOOR COOKIE C"})
    assert v and v[0][0] == "R_BEAT" and "COOKIE C is not one of the allowed words" in v[0][1]
