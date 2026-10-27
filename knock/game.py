"""One playthrough: scenes, forks, background shadows, crossings and endings."""

from __future__ import annotations

import re
import threading
import time
import tomllib
import uuid

from .describe import describe, display_name, teaser
from .engine import Timeline
from .narrator import CHAR_NAMES, narrate_crossing
from .sim import RunLog, load_shadows, pick_origin, simulate_shadow
from .state import STORY_DIR, apply_delta, initial_state, state_hash

DEFAULT_POCKET = "ticket stub"
NOT_AN_OBJECT = {"nothing", "none", "no", "idk", "dunno", "empty", "nada", "na", "n a", "nothing at all"}
LEADING = {"a", "an", "the", "my", "some", "your", "just", "only", "one"}

FORK1_EFFECT = {
    "lock": ([["set", "places.apt.locked", True]], "You lock the door."),
    "open": ([["set", "chars.mara.location", "apt"]], "You open the door. Mara steps inside."),
}
OTHER = {"lock": "open", "open": "lock", "basement": "roof", "roof": "basement"}


def sanitize_pocket(raw: str | None) -> str:
    """Free text -> a short noun phrase without article. Digits are dropped so they
    can never be confused with what a shadow writes."""
    s = re.sub(r"[^A-Za-z' \-]", " ", raw or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    words = s.split()
    while words and words[0] in LEADING:
        words.pop(0)
    s = " ".join(words[:5])[:40].strip(" -'")
    if len(s) < 2 or not re.search(r"[a-z]{2}", s) or s in NOT_AN_OBJECT:
        return DEFAULT_POCKET
    return s


def load_story() -> dict:
    with open(STORY_DIR / "story.toml", "rb") as f:
        return tomllib.load(f)


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
        self.pocket = sanitize_pocket(pocket_raw)
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
    def fmt(self, text: str) -> str:
        text = text.replace("{pocket}", self.pocket)
        if "B" in self.crossings:
            # The number that crossed over is canonical knowledge once it has been released.
            text = text.replace("{digits}", self.timeline.event(self.crossings["B"]["intrusion"]["origin_event_id"]).text)
        return text.strip()

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
        narration = narrate_crossing(self.timeline, x, self.narrator_model, self.pocket, self.seed, self.log,
                                     self.story["crossings"])
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

    def ending_text(self, which: str) -> str:
        t = self.timeline
        slots = self.story["endings"]["slots"]
        b_o = t.event(self.crossings["B"]["intrusion"]["origin_event_id"])
        d_o = t.event(self.crossings["D"]["intrusion"]["origin_event_id"])
        d = t.state("D")
        c = t.state("C")
        b = t.state("B")
        place = lambda s, pid: s["places"][pid]["name"] if pid else "nowhere"  # noqa: E731
        fill = {"pocket": self.pocket, "b_text": b_o.text, "b_on": self._on(b_o.target),
                "b_writing": self._writing(b_o.actor), "d_text": d_o.text, "d_on": self._on(d_o.target),
                "d_writing": self._writing(d_o.actor)}
        here = c["chars"]["player"]["location"]
        fill["erase_mara"] = slots["erase_mara_present" if c["chars"]["mara"]["location"] == here else "erase_mara_absent"]
        lines = []
        for oid in ("key07", "pocket"):
            h, th = self._where(c, oid), self._where(d, oid)
            obj = display_name(oid, self.pocket, "subject")
            tmpl = slots["object_same"] if h == th else slots["object_split"]
            lines.append(tmpl.replace("{Obj}", obj).replace("{here}", h).replace("{there}", th))
        fill["open_objects"] = " ".join(lines)
        ml = d["chars"]["mara"]["location"]
        if ml == d["chars"]["player"]["location"]:
            fill["open_mara"] = slots["open_mara_with_player"]
        elif ml == "unit07":
            fill["open_mara"] = slots["open_mara_unit07"]
        else:
            fill["open_mara"] = slots["open_mara_elsewhere"].replace("{mara_place}", place(d, ml))
        rank = {"present": 0, "hurt": 1, "dead": 2, "departed": 0}
        teo = max((b["chars"]["teo"]["status"], d["chars"]["teo"]["status"]), key=lambda s: rank.get(s, 0))
        fill["open_teo"] = slots["open_teo_" + {"hurt": "hurt", "dead": "dead"}.get(teo, "fine")]
        text = self.story["endings"][which]["text"]
        for _ in range(2):              # slots may contain slots
            for k, v in list(fill.items()) + [(k, v) for k, v in slots.items() if k in ("erase_marks", "open_marks")]:
                text = text.replace("{" + k + "}", v)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

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
             "clock": sc.get("clock"), "power": sc.get("power")}
        if v["kind"] == "crossing":
            cr = self.crossings[sc["shadow"]]
            v["text"] = cr["narration"]
            v["crossing"] = cr["parts"]
            v["trace"] = self.trace(cr["intrusion"]["intrusion_id"])
        return v

    def trace(self, intrusion_id: str) -> dict:
        """Everything about one intrusion: its origin event, the validator decision, released facts."""
        x = next(i for i in self.timeline.intrusions if i.intrusion_id == intrusion_id)
        origin = self.timeline.event(x.origin_event_id)
        od = origin.to_dict()
        od["desc"] = describe(od, self.timeline.state(origin.branch), self.pocket)
        before = initial_state(None, self.timeline.pocket_name)
        for e in self.timeline.lineage(origin.branch)[: origin.seq]:
            before = apply_delta(before, e.state_delta)
        return {"intrusion": x.to_dict(), "origin": od,
                "manifestation": self.timeline.event(x.manifestation_event_id).to_dict(),
                "attempts": self.attempts(origin.request_id),
                "teaser": teaser(od, before)}

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
                d["desc"] = describe(d, state, self.pocket)
                if e.source in ("model", "fallback"):
                    d["attempts"] = self.attempts(e.request_id)
                events.append(d)
        return {"labels": self.branch_labels(),
                "branches": [{"id": bid, "kind": b.kind, "parent": b.parent, "fork_seq": b.fork_seq,
                              "frozen": b.frozen} for bid, b in t.branches.items()],
                "events": events,
                "intrusions": [x.to_dict() for x in t.intrusions],
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
