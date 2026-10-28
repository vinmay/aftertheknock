"""One playthrough: scenes, forks, background shadows, crossings and endings."""

from __future__ import annotations

import re
import threading
import time
import tomllib
import uuid

from .describe import describe, display_name, display_speech, owned
from .engine import Timeline
from .narrator import CHAR_NAMES, compose, narrate_crossing
from .sim import RunLog, load_shadows, pick_origin, simulate_shadow
from .state import STORY_DIR, apply_delta, initial_state, is_plural, material_of, state_hash

DEFAULT_POCKET = "ticket stub"
NOT_AN_OBJECT = {"nothing", "none", "no", "idk", "dunno", "empty", "nada", "na", "n a", "nothing at all"}
LEADING = {"a", "an", "the", "my", "some", "your", "just", "only", "one"}

FORK1_EFFECT = {
    "lock": ([["set", "places.apt.locked", True]], "You lock the door."),
    "open": ([["set", "chars.mara.location", "apt"]], "You open the door. Mara steps inside."),
}
OTHER = {"lock": "open", "open": "lock", "basement": "roof", "roof": "basement"}


# Things people type that aren't objects you can hold. They become the same fallback object
# as an empty pocket, and the opening says so.
ABSTRACT = {"regret", "regrets", "promise", "promises", "hope", "love", "fear", "grief", "guilt", "memory",
            "memories", "time", "dreams", "dream", "secret", "secrets", "sadness", "anger", "courage", "luck",
            "silence", "loneliness", "nostalgia", "doubt", "nothing but regret", "my heart", "heart", "soul"}


DANGLING = {"from", "of", "to", "with", "for", "and", "or", "the", "a", "an", "my", "i", "that", "in", "on", "at", "by"}


def _clean(raw: str | None) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1\s*>", " ", raw or "")
    s = re.sub(r"<[^>]*>", " ", s)                       # markup is dropped, not read
    s = re.sub(r"[^A-Za-z' \-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    words = s.split()
    while words and words[0] in LEADING:
        words.pop(0)
    words = words[:5]
    while len(" ".join(words)) > 40:
        words.pop()
    while len(words) > 1 and words[-1] in DANGLING:      # don't end a phrase on "from" or "of"
        words.pop()
    return " ".join(words).strip(" -'")


def pocket_info(raw: str | None) -> tuple[str, str]:
    """Free text -> (object name without article, kind). kind: "object" | "empty" | "abstract".
    Digits are dropped so they can never be confused with what a shadow writes; everything
    is plain text and is escaped wherever it is shown."""
    s = _clean(raw)
    if len(s) < 2 or not re.search(r"[a-z]{2}", s) or s in NOT_AN_OBJECT:
        return DEFAULT_POCKET, "empty"
    if s in ABSTRACT or s.split()[-1] in ABSTRACT:
        return DEFAULT_POCKET, "abstract"
    return s, "object"


def sanitize_pocket(raw: str | None) -> str:
    return pocket_info(raw)[0]


def load_story() -> dict:
    with open(STORY_DIR / "story.toml", "rb") as f:
        return tomllib.load(f)


RECOMMENDED_REPLAY = "lock_roof_erase_4711"    # the signature moment: you lock the door, digits on your object


def replay_card(rec_id: str, rec: dict) -> dict:
    """Listing for a recorded run. The title describes what happens in it (from its first crossing);
    the path is shown only when a card is opened. Playtime isn't recorded, so it isn't shown."""
    evs = {e["event_id"]: e for b in rec["timeline"]["branches"].values() for e in b["events"]}
    name = lambda oid: "key" if oid == "key07" else rec["pocket"]  # noqa: E731
    first = evs[rec["crossings"]["B"]["intrusion"]["origin_event_id"]]
    second = evs[rec["crossings"]["D"]["intrusion"]["origin_event_id"]]
    title = f"The {name(first['target'])}, then the {name(second['target'])}"   # what crossed, in order
    return {"id": rec_id, "pocket": rec["pocket"], "title": title,
            "path": f"{rec['fork1']} / {rec['fork2']} / {rec['ending']['which']}", "seed": rec["seed"],
            "model": rec["model"].get("model"), "recommended": rec_id == RECOMMENDED_REPLAY}


def replay(record: dict) -> "Game":
    """Re-run a recorded game through the engine with the recorded model outputs."""
    from .models import ReplayModel
    model = ReplayModel(record)
    g = Game(record["pocket_raw"], record["seed"], model, background=False, run_id=record["run_id"])
    for ch in record["choices"]:
        g.choose(ch["index"])
    return g


def verify_replay(g: "Game", record: dict) -> bool:
    return g.record()["state_hashes"] == record["state_hashes"] and g.model.misses == 0


class Game:
    def __init__(self, pocket_raw: str | None, seed: int, model, narrator_model=None,
                 background: bool = True, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:10]
        self.seed = seed
        self.model = model
        self.narrator_model = narrator_model or model
        self.background = background
        self.pocket_raw = pocket_raw
        self.pocket, self.pocket_kind = pocket_info(pocket_raw)
        self.pocket_said = _clean(pocket_raw)
        self.plural = is_plural(self.pocket)
        self.material = material_of(self.pocket)
        self.story = load_story()
        self.specs = load_shadows()
        self.timeline = Timeline(f"the {self.pocket}")
        self.log = RunLog(seed, model.info())
        self.choices: list[dict] = []
        self.f1: str | None = None
        self.f2: str | None = None
        self.threads: dict[str, threading.Thread] = {}
        self.shadow_time: dict[str, dict] = {}
        self.errors: list[str] = []
        self.crossings: dict[str, dict] = {}
        self.ending: dict | None = None
        self.started = time.time()
        self.timeline.author("S", [], "11:47 PM. A knock at the door.", request_id="S:knock")
        self.scene = self.story["start"]
        self._enter(self.scene)

    # ---- text -----------------------------------------------------------
    def pocket_line(self) -> str:
        lines = self.story["pocket_lines"]
        line = lines[self.pocket_kind] if self.pocket_kind in lines else lines["object"]
        return line.replace("{said}", self.pocket_said)

    def fmt(self, text: str) -> str:
        if "{other_choice}" in text:
            for k, v in self.other_you()["slots"].items():
                text = text.replace("{" + k + "}", v)
            text = re.sub(r"\n{3,}", "\n\n", text)        # a slot with no supported variant leaves no gap
        text = text.replace("{pocket_line}", self.pocket_line())
        if "{unit07_flaw}" in text:
            a = "" if self.plural else ("an " if self.pocket[:1] in "aeiou" else "a ")
            text = text.replace("{on_the_seat}", f"On the seat {'lie' if self.plural else 'lies'} {a}{self.pocket}.")
            flaw = self.story["unit07_flaw"].get(self.material, self.story["unit07_flaw"]["unknown"])
            text = text.replace("{unit07_flaw}", flaw.replace("{it_has}", "they have" if self.plural else "it has"))
        text = text.replace("{it}", "them" if self.plural else "it")
        text = text.replace("{pocket}", self.pocket)
        if "B" in self.crossings:
            # The number that crossed over is canonical knowledge once it has been released.
            origin = self.timeline.event(self.crossings["B"]["intrusion"]["origin_event_id"])
            text = text.replace("{digits}", origin.text)
            text = text.replace("{c1_on}", self._on(origin.target))
        return owned(text.strip(), self.pocket)

    AUTHORED = "Authored scene, built from your choices and the events shown on the map."

    def other_you(self) -> dict:
        """What the other you (the protagonist of shadow B, who made the opposite first choice) says.

        Every slot comes from the player's own choices, a committed event of shadow B, or the March
        backstory; a slot with no supported variant is empty. `claims` lists the B event ids each
        stated fact rests on, so a test can hold the scene to the log."""
        sl = self.story["endings"]["slots"]
        b_events = list(self.timeline.branch("B").events)
        b_o = self.timeline.event(self.crossings["B"]["intrusion"]["origin_event_id"])
        slots = {"other_choice": sl[f"other_choice_{self.f1}"], "other_march": sl[f"other_march_{self.f1}"],
                 "other_d": sl[f"other_d_{self.f2}"], "other_detail": ""}
        claims: list[str] = []
        if self.f1 == "lock" and b_o.action == "mark" and b_o.target == "pocket":
            verb = sl["other_verb_" + self.style_for("pocket")].replace("{b_text}", b_o.text)
            it = "them" if self.plural else "it"
            verb_it, verb_the = verb.replace("{obj}", it), verb.replace("{obj}", f"the {self.pocket}")
            took = next((e for e in b_events if e.target == "pocket" and (
                (e.action == "take" and e.actor == "mara") or (e.action == "give" and e.to == "mara"))), None)
            if b_o.actor == "mara" and took:
                slots["other_detail"], claims = sl["other_detail_took"].replace("{verb_it}", verb_it), [took.event_id, b_o.event_id]
            elif b_o.actor == "mara":
                slots["other_detail"], claims = sl["other_detail_watched"].replace("{verb_the}", verb_the), [b_o.event_id]
            elif b_o.actor == "player":
                slots["other_detail"], claims = sl["other_detail_self"].replace("{verb_it}", verb_it).replace(
                    "{it_sub}", "they" if self.plural else "it"), [b_o.event_id]
        elif self.f1 == "open":
            teo_open = next((e for e in b_events if e.action == "open" and e.target == "teo_apt" and e.actor == "teo"), None)
            if teo_open:
                slots["other_detail"], claims = sl["other_detail_teo_open"], [teo_open.event_id]
            elif b_o.action == "mark" and b_o.target == "key07":
                slots["other_detail"], claims = sl["other_detail_tag"].replace("{b_text}", b_o.text), [b_o.event_id]
        return {"slots": slots, "claims": claims}

    def canon(self) -> str:
        return self.timeline.canonical()

    # ---- shadows ----------------------------------------------------------
    def _start_shadow(self, bid: str, spec_key: str) -> None:
        def run():
            t0 = time.perf_counter()
            try:
                simulate_shadow(self.timeline, bid, spec_key, self.model, self.seed, self.log, self.specs)
            except Exception as exc:  # never lose the story to a model failure
                self.errors.append(f"{bid}: {exc!r}")
                raise
            finally:
                self.shadow_time[bid]["sim_s"] = time.perf_counter() - t0
        self.shadow_time[bid] = {"spec": spec_key, "started": time.perf_counter()}
        if self.background:
            th = threading.Thread(target=run, name=f"shadow-{bid}", daemon=True)
            self.threads[bid] = th
            th.start()
        else:
            run()

    def _wait_shadow(self, bid: str) -> float:
        t0 = time.perf_counter()
        th = self.threads.get(bid)
        if th is not None:
            th.join()
        waited = time.perf_counter() - t0
        self.shadow_time[bid]["wait_s"] = waited
        return waited

    # ---- scenes -----------------------------------------------------------
    def _enter(self, sid: str) -> None:
        self.scene = sid
        sc = self.story["scenes"][sid]
        evidence = []
        if sc.get("evidence") == "crossing1":
            evidence = [self.crossings["B"]["intrusion"]["manifestation_event_id"]]
        for i, op in enumerate(sc.get("effects", [])):
            summary = self.fmt(sc["effect_summary"]) if "effect_summary" in sc else self._effect_summary(op)
            self.timeline.author(self.canon(), [op], summary, request_id=f"{self.canon()}:{sid}:{i}",
                                 evidence=evidence)
        if sc.get("kind") == "crossing":
            self._cross(sc["shadow"])

    def _effect_summary(self, op: list) -> str:
        _, path, value = op
        kind, eid, fld = path.split(".")
        s = self.timeline.state(self.canon())
        if kind == "chars" and fld == "location":
            who = "You go" if eid == "player" else f"{CHAR_NAMES.get(eid, eid)} goes"
            return f"{who} to {s['places'][value]['name']}."
        return f"{eid} {fld} = {value}"

    def _cross(self, shadow: str) -> None:
        waited = self._wait_shadow(shadow)
        origin = pick_origin(self.timeline, shadow)
        x = self.timeline.cross(shadow, origin.event_id)
        t0 = time.perf_counter()
        oid = self.timeline.event(x.origin_event_id).target
        narration = narrate_crossing(self.timeline, x, self.narrator_model, self.pocket, self.seed, self.log,
                                     self.story["crossings"], style=self.style_for(oid),
                                     avoid=next((c["parts"].get("color") for c in self.crossings.values()), None))
        narration["parts"]["material"] = self.material_for(oid)
        if shadow == "B":                       # the player's own first choice, mirrored; no shadow fact
            narration["parts"]["echo"] = self.story["endings"]["slots"][f"echo_{self.f1}"]
            narration["text"] = compose(narration["parts"], narration["color"])
        for k in ("before", "after", "who"):
            narration["parts"][k] = owned(narration["parts"][k], self.pocket)
        narration["text"] = owned(narration["text"], self.pocket)
        self.crossings[shadow] = {"intrusion": x.to_dict(), "narration": narration["text"],
                                  "parts": narration["parts"],
                                  "narration_source": narration["source"], "raw": narration["raw"],
                                  "prompt": narration["messages"],
                                  "released_at": self.timeline.released_facts(x.target_branch),
                                  "wait_s": waited, "narrate_s": time.perf_counter() - t0}

    def choose(self, index: int) -> dict:
        sc = self.story["scenes"][self.scene]
        choices = sc.get("choices", [])
        if not 0 <= index < len(choices):
            raise ValueError(f"no choice {index} in scene {self.scene}")
        ch = choices[index]
        self.choices.append({"scene": self.scene, "index": index, "label": self.fmt(ch["label"]),
                             "t": round(time.time() - self.started, 2)})
        if "fork" in ch:
            self._fork(sc["kind"], ch["fork"])
        elif "ending" in ch:
            self._end(ch["ending"])
        else:
            nxt = ch["next"].replace("$fork2", f"{self.f1}_fork2")
            self._enter(nxt)
        return self.view()

    def _fork(self, kind: str, choice: str) -> None:
        if kind == "fork1":
            self.f1 = choice
            spec = "B." + OTHER[choice]
            self.timeline.fork("S", budget=len(self.specs[spec]["steps"]))
            delta, summary = FORK1_EFFECT[choice]
            self.timeline.author("A", delta, summary, request_id="A:fork1")
            self._start_shadow("B", spec)
            self._enter(f"{choice}_1")
        else:
            self.f2 = choice
            spec = f"D.{self.f1}.{OTHER[choice]}"
            self.timeline.fork("A", budget=len(self.specs[spec]["steps"]))
            self.timeline.author("C", [], f"You choose the {choice}.", request_id="C:fork2")
            self._start_shadow("D", spec)
            self._enter(f"{self.f1}_{choice}")

    # ---- endings ----------------------------------------------------------
    def _end(self, which: str) -> None:
        self.scene = f"ending_{which}"
        self.ending = {"which": which, "text": self.ending_text(which)}

    def material_for(self, oid: str) -> str:
        """The key's tag is paper; the pocket object is whatever the player said it was."""
        return "paper" if oid == "key07" else self.material

    def style_for(self, oid: str) -> str:
        """How a mark is made on it: written (paper), scratched (metal, small solids), marked (unknown)."""
        return {"paper": "paper", "metal": "scratched", "small": "scratched"}.get(self.material_for(oid), "unknown")

    def _mark_line(self, text: str, oid: str, actor: str) -> str:
        style = self.style_for(oid)
        hand = "your own hand" if actor == "player" else f"{CHAR_NAMES.get(actor, 'someone')}'s hand"
        line = self.story["endings"]["slots"][f"mark_line_{style}"]
        for k, v in {"text": text, "on": self._on(oid), "writing": self._writing(actor), "hand": hand}.items():
            line = line.replace("{" + k + "}", v)
        return line

    def _writing(self, actor: str) -> str:
        return "your own handwriting" if actor == "player" else f"{CHAR_NAMES.get(actor, 'someone')}'s handwriting"

    def _on(self, oid: str) -> str:
        return display_name(oid, self.pocket, "on")

    def _where(self, s: dict, oid: str) -> str:
        """Where an object is in a given night, in plain words, from committed state only."""
        o = s["objects"][oid]
        here = s["chars"]["player"]["location"]
        if o["holder"] == "player":
            return "in your hand"
        if o["holder"]:
            who = CHAR_NAMES.get(o["holder"], o["holder"])
            if s["chars"][o["holder"]]["location"] == here:
                return f"in {who}'s hand, beside you"
            return f"with {who}, in {s['places'][s['chars'][o['holder']]['location']]['name']}"
        if o["location"] == here:
            return "at your feet"
        return f"in {s['places'][o['location']]['name']}" if o["location"] else "nowhere you can find"

    def _char_where(self, s: dict, cid: str) -> str:
        loc = s["chars"][cid]["location"]
        return "beside you" if loc == s["chars"]["player"]["location"] else f"in {s['places'][loc]['name']}"

    def ending_text(self, which: str) -> str:
        t = self.timeline
        slots = self.story["endings"]["slots"]
        b_o = t.event(self.crossings["B"]["intrusion"]["origin_event_id"])
        d_o = t.event(self.crossings["D"]["intrusion"]["origin_event_id"])
        d = t.state("D")
        c = t.state("C")
        b = t.state("B")
        place = lambda s, pid: s["places"][pid]["name"] if pid else "nowhere"  # noqa: E731
        fill = {"pocket": self.pocket, "b_text": b_o.text, "b_on": self._on(b_o.target), "d_text": d_o.text,
                "d_on": self._on(d_o.target), "b_line": self._mark_line(b_o.text, b_o.target, b_o.actor),
                "d_line": self._mark_line(d_o.text, d_o.target, d_o.actor)}
        here = c["chars"]["player"]["location"]
        fill["erase_mara"] = slots["erase_mara_present" if c["chars"]["mara"]["location"] == here else "erase_mara_absent"]
        lines = []
        for oid in ("key07", "pocket"):
            h, th = self._where(c, oid), self._where(d, oid)
            plural = oid == "pocket" and self.plural
            words = {"{Obj}": display_name(oid, self.pocket, "subject"), "{obj}": display_name(oid, self.pocket),
                     "{here}": h, "{there}": th, "{is}": "are" if plural else "is", "{it}": "they" if plural else "it",
                     "{It}": "They" if plural else "It", "{was}": "were" if plural else "was"}
            line = slots["object_same"] if h == th else slots["object_split"]
            for k, v in words.items():
                line = line.replace(k, v)
            lines.append(line)
        fill["open_objects"] = " ".join(lines)
        mh, mt = self._char_where(c, "mara"), self._char_where(d, "mara")
        fill["open_mara"] = (slots["mara_same"] if mh == mt else slots["mara_split"]).replace(
            "{here}", mh).replace("{there}", mt)
        rank = {"present": 0, "hurt": 1, "dead": 2, "departed": 0}
        teo = max((b["chars"]["teo"]["status"], d["chars"]["teo"]["status"]), key=lambda s: rank.get(s, 0))
        fill["open_teo"] = slots["open_teo_" + {"hurt": "hurt", "dead": "dead"}.get(teo, "fine")]
        text = self.story["endings"][which]["text"]
        for _ in range(2):              # slots may contain slots
            for k, v in list(fill.items()) + [(k, v) for k, v in slots.items() if k in ("erase_marks", "open_marks")]:
                text = text.replace("{" + k + "}", v)
        return owned(re.sub(r"\n{3,}", "\n\n", text).strip(), self.pocket)

    # ---- views ------------------------------------------------------------
    def view(self) -> dict:
        if self.ending:
            return {"scene": self.scene, "kind": "ending", "text": self.ending["text"], "choices": [],
                    "ending": self.ending["which"],
                    "clock": self.story["endings"][self.ending["which"]].get("clock")}
        sc = self.story["scenes"][self.scene]
        v = {"scene": self.scene, "kind": sc.get("kind", "scene"),
             "text": self.fmt(sc.get("text", "")),
             "choices": [self.fmt(c["label"]) for c in sc.get("choices", [])],
             # Stable ids for presentation cues (e.g. the lock/open sound), never parsed from prose.
             "choice_ids": [c.get("fork") or c.get("ending") or c.get("next") for c in sc.get("choices", [])],
             "clock": sc.get("clock"), "power": sc.get("power"),
             # Presentation cues: a one-off sound on entering, and the room held low (desired state).
             "sound": sc.get("sound"), "hush": bool(sc.get("hush"))}
        if sc.get("authored"):
            v["authored"] = self.AUTHORED
        if v["kind"] == "crossing":
            cr = self.crossings[sc["shadow"]]
            v["text"] = cr["narration"]
            v["crossing"] = cr["parts"]
            v["trace"] = self.trace(cr["intrusion"]["intrusion_id"])
        return v

    def authored_seen(self) -> list[dict]:
        """Authored scenes the player has reached, labelled for the evidence view. They add no events."""
        seen = [c["scene"] for c in self.choices] + [self.scene]
        return [{"scene": sid, "label": self.AUTHORED} for sid in dict.fromkeys(seen)
                if self.story["scenes"].get(sid, {}).get("authored")]

    BRANCH_PHRASE = {"open": "the night where you opened the door", "lock": "the night where you kept the door locked",
                     "basement": "the night where you went down to the basement", "roof": "the night where you went up to the roof"}

    def _state_before(self, e) -> dict:
        s = initial_state(None, self.timeline.pocket_name)
        for prior in self.timeline.lineage(e.branch)[: e.seq]:
            s = apply_delta(s, prior.state_delta)
        return s

    def memory(self, intrusion_id: str) -> dict:
        """A plain-language memory of where a crossing came from, built only from committed events."""
        t = self.timeline
        x = next(i for i in t.intrusions if i.intrusion_id == intrusion_id)
        o = t.event(x.origin_event_id)
        branch_choice = OTHER[self.f1] if x.origin_branch == "B" else OTHER[self.f2]
        night = self.BRANCH_PHRASE[branch_choice]
        name = lambda c: "the other you" if c == "player" else CHAR_NAMES.get(c, c)  # noqa: E731
        obj = display_name(o.target, self.pocket)
        # The committed event that put the object in the writer's hands, if it happened in this shadow.
        pre = None
        for e in reversed(t.branch(o.branch).events[: t.branch(o.branch).events.index(o)]):
            if ["set", f"objects.{o.target}.holder", o.actor] in [list(d) for d in e.state_delta]:
                pre = e
                break
        writer = name(o.actor)
        cap = lambda w: w[0].upper() + w[1:]  # noqa: E731
        style = self.style_for(o.target)
        it = "them" if (o.target == "pocket" and self.plural) else "it"
        mark = f"\u201c{o.text}\u201d"
        made = {"paper": f"wrote {mark} on " + ("its paper tag" if o.target == "key07" else it),
                "scratched": f"scratched {mark} into {it}", "unknown": f"marked {it} with {mark}"}[style]
        made_on = {"paper": f"wrote {mark} on {display_name(o.target, self.pocket, 'on')}",
                   "scratched": f"scratched {mark} into {display_name(o.target, self.pocket, 'on')}",
                   "unknown": f"marked {display_name(o.target, self.pocket, 'on')} with {mark}"}[style]
        if pre is not None and pre.action == "take":
            what = f"{cap(writer)} took {obj} and {made}."
        elif pre is not None and pre.action == "give":
            what = f"{cap(name(pre.actor))} gave {obj} to {writer}, and {writer} {made}."
        else:
            what = f"{cap(writer)} {made_on}."
        kind = "numbers" if o.text.isdigit() else "words"
        before = self._state_before(o)
        there = before["places"][before["chars"][o.actor]["location"]]["name"]
        man = t.event(x.manifestation_event_id)
        here_state = self._state_before(man)
        here = here_state["places"][here_state["chars"]["player"]["location"]]["name"]
        what = what[0].lower() + what[1:] if what.startswith("The other") else what
        scene = self.flashback(o)
        what = owned(what, self.pocket)
        scene = [dict(line, line=owned(line["line"], self.pocket)) for line in scene] if scene else scene
        return {"memory": f"In {night}, {what} This is where the {kind} came from.",
                "night": night, "scene": scene,
                "closing": self.story["flashback"][f"closing_{kind}"],
                "source": f"Written there: {night}, {there}",
                "dest": f"Appeared here: your night, {here}",
                "inscription": o.text, "object": owned(display_name(o.target, self.pocket), self.pocket)}

    def flashback(self, origin, max_lines: int = 5) -> list[dict] | None:
        """The other night, up to the mark, as short lines rendered from committed events only.

        Returns None (use the plain sentence) if any event has no template."""
        tmpl = self.story["flashback"]
        events = self.timeline.branch(origin.branch).events
        events = events[: events.index(origin) + 1]
        name = lambda c: "the other you" if c == "player" else CHAR_NAMES.get(c, c)  # noqa: E731
        cap = lambda w: w[0].upper() + w[1:]  # noqa: E731
        lines = []
        prev_actor, prev_obj = None, None
        pronoun = {"mara": "She", "teo": "He"}
        for e in events:
            if e.source == "story":
                lines.append({"line": e.summary, "event_id": e.event_id, "action": "story"})
                prev_actor, prev_obj = None, None
                continue
            before = self._state_before(e)
            places = before["places"]
            key = e.action
            if e.action == "speak":
                key = "speak_aloud" if e.target in places else "speak_to"
            is_obj = e.target in before["objects"]
            if e.action == "mark" and is_obj:
                style = self.style_for(e.target)
                again = e.target == prev_obj
                key = ("mark_again" if again else "mark") + ("" if style == "paper" else f"_{style}")
            if key not in tmpl:
                return None
            prev = before["objects"].get(e.target, {}).get("holder") if e.action == "take" else None
            # Deterministic polish: the same person twice in a row becomes a pronoun (not inside speech lines).
            use_pronoun = e.actor == prev_actor and e.actor in pronoun and not key.startswith("speak")
            plural = e.target == "pocket" and self.plural
            slots = {"A": pronoun[e.actor] if use_pronoun else cap(name(e.actor)), "T": name(e.to or e.target),
                     "obj": display_name(e.target, self.pocket) if is_obj else "",
                     "on": display_name(e.target, self.pocket, "on") if is_obj else "",
                     "it": ("them" if plural else "it") if is_obj else "",
                     "place": places[e.target]["name"] if e.target in places else "",
                     "text": display_speech(e.text) if e.action == "speak" else e.text,
                     "from": f" from {name(prev)}" if prev else ""}
            line = tmpl[key]
            for k, v in slots.items():
                line = line.replace("{" + k + "}", v)
            lines.append({"line": line, "event_id": e.event_id, "action": e.action})
            prev_actor, prev_obj = e.actor, (e.target if is_obj else None)
        if len(lines) > max_lines:          # keep the start of the night and the lines leading to the mark
            lines = lines[:1] + lines[-(max_lines - 1):]
        return lines

    def trace(self, intrusion_id: str) -> dict:
        """Everything about one intrusion: its origin event, the validator decision, released facts."""
        x = next(i for i in self.timeline.intrusions if i.intrusion_id == intrusion_id)
        origin = self.timeline.event(x.origin_event_id)
        od = origin.to_dict()
        od["desc"] = describe(od, self.timeline.state(origin.branch), self.pocket, self.style_for)
        return {"intrusion": x.to_dict(), "origin": od, "memory": self.memory(intrusion_id),
                "manifestation": self.timeline.event(x.manifestation_event_id).to_dict(),
                "attempts": self.attempts(origin.request_id)}

    def branch_labels(self) -> dict:
        f1, f2 = self.f1, self.f2
        door = {"lock": "you locked the door", "open": "you opened the door"}
        labels = {"S": "11:47, before the choice"}
        if f1:
            labels["A"] = f"Your night: {door[f1]}"
            labels["B"] = f"The other night: {door[OTHER[f1]]}"
        if f2:
            labels["C"] = f"Your night: you went to the {f2}"
            labels["D"] = f"The other night: you went to the {OTHER[f2]}"
        return labels

    def fork_labels(self) -> list[dict]:
        """The actual choice text at each fork: the one taken and the one the shadow lived."""
        out = []
        for ch in self.choices:
            sc = self.story["scenes"].get(ch["scene"], {})
            if sc.get("kind") in ("fork1", "fork2"):
                labels = [self.fmt(c["label"]) for c in sc["choices"]]
                out.append({"at": "S" if sc["kind"] == "fork1" else "A", "taken": labels[ch["index"]],
                            "not_taken": labels[1 - ch["index"]]})
        return out

    def verify(self) -> dict:
        """Re-run this game's own log through the engine and compare every timeline's state hash."""
        import json
        rec = json.loads(json.dumps(self.record()))
        again = replay(rec)
        return {"verified": verify_replay(again, rec), "hashes": rec["state_hashes"],
                "replayed": again.record()["state_hashes"]}

    def attempts(self, request_id: str) -> list[dict]:
        return [{"attempt": r.get("attempt"), "proposal": r.get("proposal"), "kind": r["kind"],
                 "decision": r.get("decision"), "raw": r.get("raw"),
                 "latency_s": (r.get("meta") or {}).get("latency_s")}
                for r in self.log.records
                if r.get("request_id") == request_id and r["kind"] in ("proposal", "fallback", "skipped")]

    def map_data(self) -> dict:
        t = self.timeline
        events = []
        for bid, b in t.branches.items():
            state = t.state(bid)
            for e in b.events:
                d = e.to_dict()
                d["desc"] = owned(describe(d, state, self.pocket, self.style_for), self.pocket)
                if e.source in ("model", "fallback"):
                    d["attempts"] = self.attempts(e.request_id)
                events.append(d)
        return {"labels": self.branch_labels(),
                "branches": [{"id": bid, "kind": b.kind, "parent": b.parent, "fork_seq": b.fork_seq,
                              "frozen": b.frozen} for bid, b in t.branches.items()],
                "events": events,
                "intrusions": [x.to_dict() | {"memory": self.memory(x.intrusion_id)} for x in t.intrusions],
                "forks": self.fork_labels(),
                "narration": {k: {"text": c["narration"], "source": c["narration_source"], "raw": c["raw"]}
                              for k, c in self.crossings.items()}}

    def record(self) -> dict:
        return {"run_id": self.run_id, "seed": self.seed, "model": self.log.model,
                "pocket_raw": self.pocket_raw, "pocket": self.pocket, "fork1": self.f1, "fork2": self.f2,
                "choices": self.choices, "timeline": self.timeline.to_dict(),
                "state_hashes": {bid: state_hash(self.timeline.state(bid)) for bid in self.timeline.branches},
                "crossings": self.crossings, "ending": self.ending,
                "shadow_time": {k: {kk: vv for kk, vv in v.items() if kk != "started"}
                                for k, v in self.shadow_time.items()},
                "errors": self.errors, "log": self.log.records}
