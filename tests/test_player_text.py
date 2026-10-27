"""Player-facing text never shows internal ids, and endings agree with committed state.

Covers every committed replay, every saved eval run, and fresh offline playthroughs on
all paths with awkward pocket inputs.
"""

import json
import re
from pathlib import Path

import pytest

from knock.describe import display_name
from knock.game import Game
from knock.models import RandomModel, ReplayModel

ROOT = Path(__file__).resolve().parent.parent
RECORDS = sorted((ROOT / "data" / "runs" / "replays").glob("*.json")) + sorted((ROOT / "eval" / "results" / "runs").glob("*.json"))
POCKETS = ["nothing", "my keys", "two pennies", "a very long folded note from someone I used to know well",
           "<b>receipt</b>", "Gloves"]
PATHS = [(f1, f2, end) for f1 in ("lock", "open") for f2 in ("basement", "roof") for end in ("erase", "open")]

# Internal ids and state paths. "pocket" is fine in prose ("your pocket") but never as an object.
FORBIDDEN = [
    re.compile(r"\b(key07|unit07|teo_apt|apt)\b"),
    re.compile(r"\b(objects|chars|places|events)\.\w"),
    re.compile(r"\bplayer\b", re.I),
    re.compile(r"\b(the|a|on|from|holds?|took|takes|wrote on) pocket\b", re.I),
    re.compile(r"\b[A-D]\.\d\d\b"),
]


def player_facing(g: Game, views: list[dict]) -> list[str]:
    out = []
    for v in views:
        out += [v["text"], *v["choices"]]
        if v.get("crossing"):
            c = v["crossing"]
            out += [c["before"], c["after"], c["who"], c.get("color") or ""]
    m = g.map_data()
    out += list(m["labels"].values())
    out += [e["desc"] for e in m["events"]]
    for x in m["intrusions"]:
        out += [x["memory"]["memory"], x["memory"]["source"], x["memory"]["dest"], x["memory"]["object"]]
    for f in m["forks"]:
        out += [f["taken"], f["not_taken"]]
    out.append(g.ending_text("erase"))
    out.append(g.ending_text("open"))
    return [s for s in out if s]


def play_views(g: Game, choices) -> list[dict]:
    views = [g.view()]
    for idx in choices:
        views.append(g.choose(idx))
    return views


def offline_runs():
    for i, (f1, f2, end) in enumerate(PATHS):
        for pocket in POCKETS[: 3 if i % 2 else 6]:
            g = Game(pocket, 30 + i, RandomModel(), background=False)
            want = {"fork1": f1, "fork2": f2, "final": end}
            views = [g.view()]
            while views[-1]["choices"]:
                sc = g.story["scenes"][g.scene]
                w = want.get(sc.get("kind"))
                views.append(g.choose(next((k for k, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0)))
            yield f"offline {f1}/{f2}/{end} {pocket!r}", g, views


def recorded_runs():
    for p in RECORDS:
        rec = json.loads(p.read_text())
        g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False, run_id=rec["run_id"])
        yield p.name, g, play_views(g, [c["index"] for c in rec["choices"]])


def all_runs():
    yield from offline_runs()
    yield from recorded_runs()


@pytest.mark.parametrize("name,g,views", list(all_runs()), ids=lambda v: v if isinstance(v, str) else "")
def test_no_internal_ids_in_player_text(name, g, views):
    for text in player_facing(g, views):
        for pat in FORBIDDEN:
            m = pat.search(text)
            assert not m, f"{name}: {m.group(0)!r} in {text!r}"


@pytest.mark.parametrize("name,g,views", list(all_runs()), ids=lambda v: v if isinstance(v, str) else "")
def test_ending_matches_committed_state(name, g, views):
    t = g.timeline
    c, d = t.state("C"), t.state("D")
    opened = g.ending_text("open")
    for oid in ("key07", "pocket"):
        here, there = g._where(c, oid), g._where(d, oid)
        if here == there:
            assert f"{here}, in both nights." in opened, (name, oid)
        else:
            # The paradox is stated before the positions, never implied.
            i = opened.index(f"The two nights no longer agree about {display_name(oid, g.pocket)}.")
            assert opened.index(here, i) < opened.index(there, opened.index("In the other night", i)), (name, oid)
    mh, mt = g._char_where(c, "mara"), g._char_where(d, "mara")
    if mh == mt:
        assert f"Mara is {mh}, in both nights." in opened, name
    else:
        assert f"The worlds no longer agree on where Mara is. Here, she is {mh}. In the other night, she is {mt}." in opened, name
    # Only the marks that actually crossed are mentioned, each with its real writer.
    crossed = {}
    for shadow in ("B", "D"):
        o = t.event(g.crossings[shadow]["intrusion"]["origin_event_id"])
        crossed[o.text] = o
        assert f'"{o.text}" on {display_name(o.target, g.pocket, "on")}, in {g._writing(o.actor)}' in opened, name
    for ending in (opened, g.ending_text("erase")):
        quoted = set(re.findall(r'"([^"]+)"', ending)) - {"Finally."}
        assert quoted == set(crossed), (name, quoted)
        # It ends on one short line.
        last = ending.strip().split("\n")[-1]
        assert len(last) <= 45 and last.count(".") == 1, (name, last)


@pytest.mark.parametrize("name,g,views", list(all_runs()), ids=lambda v: v if isinstance(v, str) else "")
def test_flashback_lines_come_from_committed_events(name, g, views):
    """Every action in a flashback is an event committed in that shadow, in order, ending on the mark."""
    verbs = {"goes": "move", "says": "speak", "hands": "give", "takes": "take", "writes": "mark",
             "unlocks": "open", "hides": "hide", "finds": "discover", "hurts": "harm"}
    t = g.timeline
    for x in t.intrusions:
        mem = g.memory(x.intrusion_id)
        assert mem["scene"], name
        shadow = {e.event_id: e for e in t.branch(x.origin_branch).events}
        for line in mem["scene"]:
            e = shadow[line["event_id"]]                       # every line cites a committed event
            assert e.action == line["action"], name
            if e.source == "story":
                assert line["line"] == e.summary
                continue
            found = [a for v, a in verbs.items() if re.search(rf"\b{v}\b", line["line"])]
            assert found == [e.action], (name, line["line"], e.action)
            if e.action in ("speak", "mark"):
                assert e.text in line["line"], name           # words and marks verbatim
        last = mem["scene"][-1]
        assert last["event_id"] == x.origin_event_id and f"\u201c{t.event(x.origin_event_id).text}\u201d" in last["line"]
        # "took" only when a take happened.
        if not any(e.action == "take" for e in shadow.values()):
            assert "takes" not in " ".join(l["line"] for l in mem["scene"])


@pytest.mark.parametrize("name,g,views", list(all_runs()), ids=lambda v: v if isinstance(v, str) else "")
def test_crossing_text_only_claims_what_your_night_saw(name, g, views):
    """A crossing never assumes you saw an object your own night never showed you."""
    from knock.narrator import seen_before
    for x in g.timeline.intrusions:
        parts = g.crossings[x.origin_branch]["parts"]
        if parts["situation"].startswith("arrived"):
            seen = seen_before(g.timeline, x, parts["object"])
            assert parts["situation"] == ("arrived_seen" if seen else "arrived_unseen"), name
            if not seen:
                assert "last saw" not in parts["after"] and "last time you saw" not in parts["after"], name
