"""Shadow simulation: the model proposes, the validator decides, the engine commits."""

from __future__ import annotations

import json
import random
import re
import time
import tomllib
from dataclasses import dataclass, field

from .engine import Timeline
from .models import prompt_hash
from .state import STORY_DIR, INACTIVE, apply_delta
from .narrator import BANNED_PHRASES, banned_in, normalize
from .validator import ACTIONS

MAX_ATTEMPTS = 3
WORDS_PATTERN = "^[A-Z][A-Z' ]{2,22}$"
DIGITS_PATTERN = "^[0-9]{4}$"
# Seeded hints that steer what a mark means, so the model's digits and words vary by seed.
MEANINGS = {
    "digits": ["the year someone disappeared", "a date written as month and day", "an apartment number in another building",
               "the age someone will never reach", "the last four digits of a phone number nobody answers",
               "a bus route that stopped running", "the number of days someone waited", "a locker combination"],
    "words": ["a warning", "a plea", "an instruction", "an apology", "a name and a place", "a promise",
              "a question", "something the other you wishes they had said"],
    "spoken": ["a confession", "directions to somewhere", "something a parent says to a child", "an apology",
               "a reason", "a promise", "a name, said like a question", "a thank-you that comes too late"],
}
FALLBACK_WORDS = ["SHE IS STILL HERE", "WE STAYED", "NOT THIS NIGHT", "OPEN IT", "COUNT THE STAIRS"]
FALLBACK_SPOKEN = ["It was always going to be you.", "Count the stairs on the way down.", "I kept your place.",
                   "Leave the light off.", "You can stop holding the door now."]
SPOKEN_PATTERN = "^[A-Za-z][A-Za-z ,.'?!-]{2,70}$"
MAX_SPOKEN_WORDS = 12
MIN_SPOKEN_WORDS = 4
SPEAK_EXAMPLES = {"the third stair is the loud one.", "she always takes the long way down."}

SYSTEM = ("You write one event at a time for a hidden timeline in a quiet horror story. "
          "Reply with JSON only. Use only the ids listed. The summary is one short past-tense "
          "sentence; call 'player' \"the other you\".")


def load_shadows() -> dict:
    with open(STORY_DIR / "shadows.toml", "rb") as f:
        raw = tomllib.load(f)
    specs = {}
    for shadow, group in raw.items():
        for k, v in group.items():
            if "steps" in v:
                specs[f"{shadow}.{k}"] = v
            else:
                for k2, v2 in v.items():
                    specs[f"{shadow}.{k}.{k2}"] = v2
    return specs


@dataclass
class RunLog:
    seed: int
    model: dict
    records: list[dict] = field(default_factory=list)

    def add(self, **rec) -> None:
        self.records.append(rec)

    def to_dict(self) -> dict:
        return {"seed": self.seed, "model": self.model, "records": self.records}


def describe(state: dict) -> str:
    chars, objects, places = state["chars"], state["objects"], state["places"]
    lines = ["People:"]
    for cid, c in chars.items():
        held = [o for o, ob in objects.items() if ob["holder"] == cid]
        bits = [f"in {c['location']}"]
        if held:
            bits.append("holds " + ", ".join(held))
        if c["status"] != "present":
            bits.append(c["status"])
        if c["status"] not in INACTIVE:
            here = c["location"]
            moves = [p for p in places[here]["adjacent"]
                     if not places[p]["locked"] or objects.get(places[p]["key"] or "", {}).get("holder") == cid]
            near = [o for o, c2 in chars.items() if o != cid and c2["location"] == here and c2["status"] not in INACTIVE]
            bits.append("can move to " + (", ".join(moves) or "nowhere"))
            if near:
                bits.append("is with " + ", ".join(near))
        lines.append(f"- {cid} ({c['name']}): " + "; ".join(bits))
    lines.append("Objects:")
    for oid, o in objects.items():
        extra = f", marked: {', '.join(repr(m) for m in o['marks'])}" if o["marks"] else ""
        where = f"held by {o['holder']}" if o["holder"] else f"lying in {o['location']}"
        lines.append(f"- {oid}: {o['name']} ({where}{extra})")
    lines.append("Places (adjacent):")
    for pid, p in places.items():
        lock = " LOCKED" + (f", opens with {p['key']}" if p["key"] else ", opens from inside") if p["locked"] else ""
        lines.append(f"- {pid}: {p['name']}{lock} -> {', '.join(p['adjacent'])}")
    return "\n".join(lines)


def build_messages(timeline: Timeline, bid: str, spec: dict, step: dict, feedback: str | None,
                   flavor: str | None = None) -> list[dict]:
    b = timeline.branch(bid)
    recent = [e for e in b.events][-4:]
    hist = "\n".join(f"- {e.summary or e.action}" for e in recent) or "- (nothing yet)"
    parts = [
        f"Timeline {bid}. 11:47 PM, a storm, an old apartment building.",
        spec["premise"],
        "Mara wants to reach storage unit 07 in the basement, with someone beside her.",
        describe(b.state),
        f"So far in this timeline:\n{hist}",
        "Actions and their target: move/open -> a place id (open unlocks a door); "
        "take/give/mark/hide/discover -> an object id; speak/harm -> a person id "
        "(or, for speak, the place you are in, to say something aloud). "
        "take = get an object from someone with you; give = hand an object you hold to the recipient; "
        "mark = write text on an object you hold.",
        "Rules: act only where you are; give, mark or hide only what you hold; move one step to an "
        "adjacent place; a locked place needs its key.",
        f"Next event: {step['hint']}"
        + (f" What is {'said' if step.get('speak') else 'written'} is {flavor}." if flavor else ""),
        "Never use these phrases: " + ", ".join(f'"{p}"' for p in BANNED_PHRASES[::2]) + ".",
        "Fields: summary (what happens), actor, action, target, recipient (person receiving, for give "
        "only, else \"\"), text (words spoken or written, else \"\").",
    ]
    if feedback:
        parts.append(f"Your last proposal was rejected: {feedback}. Do not repeat it. "
                     "Propose a different event that is possible right now.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def step_schema(state: dict, step: dict) -> dict:
    chars = [c for c, v in state["chars"].items() if v["status"] not in INACTIVE]
    ids = list(state["chars"]) + list(state["objects"]) + list(state["places"])
    props = {
        "summary": {"type": "string", "maxLength": 160},
        "actor": {"enum": chars},
        "action": {"enum": list(step.get("actions", ACTIONS))},
        "target": {"enum": ids},
        "recipient": {"enum": [""] + list(state["chars"])},
        "text": {"type": "string", "maxLength": 60},
    }
    if step.get("mark"):
        held = {o: v["holder"] for o, v in state["objects"].items()
                if v["markable"] and v["holder"] in chars}
        prefer = step.get("prefer")
        if prefer in held:
            held = {prefer: held[prefer]}
        props["actor"] = {"enum": sorted(set(held.values())) or chars}
        props["action"] = {"enum": ["mark"]}
        props["target"] = {"enum": sorted(held) or [o for o, v in state["objects"].items() if v["markable"]]}
        props["text"] = {"type": "string",
                         "pattern": DIGITS_PATTERN if step["mark"] == "digits" else WORDS_PATTERN}
    if step.get("speak"):
        props["action"] = {"enum": ["speak"]}
        props["text"] = {"type": "string", "pattern": SPOKEN_PATTERN}
    return {"type": "object", "properties": props, "required": list(props)}


def goal_unmet(step: dict, after: dict) -> str | None:
    """Optional step goal checked on the state the event would produce.

    Goals: "together <a> <b>"  |  "holds <char> <object>"  |  "unlocked <place>"
    """
    goal = step.get("goal")
    if not goal:
        return None
    kind, *args = goal.split()
    if kind == "together":
        a, b = args
        if after["chars"][a]["location"] != after["chars"][b]["location"]:
            return f"after this event {a} and {b} must be in the same place"
    elif kind == "unlocked":
        if after["places"][args[0]]["locked"]:
            return f"after this event {args[0]} must be unlocked"
    elif kind == "holds":
        who, obj = args
        if after["objects"][obj]["holder"] != who:
            return f"after this event {who} must hold {obj}"
    return None


def beat_violations(step: dict, p: dict, decision=None, state: dict | None = None) -> list[tuple[str, str]]:
    """Story constraints for this step, separate from world rules."""
    out = []
    if decision is not None and decision.accepted and state is not None:
        unmet = goal_unmet(step, apply_delta(state, decision.delta))
        if unmet:
            out.append(("R_BEAT", unmet))
    if p.get("action") not in step.get("actions", ACTIONS) and not (step.get("mark") or step.get("speak")):
        out.append(("R_BEAT", f"action {p.get('action')!r} not allowed for this step"))
    if step.get("speak") and normalize(p.get("text") or "") in SPEAK_EXAMPLES:
        out.append(("R_BEAT", "that is one of the examples; write a new sentence"))
    banned = banned_in(p.get("text") or "")
    if banned:
        out.append(("R_BEAT", f"banned phrase {banned[0]!r}; write something specific to this night"))
    if step.get("speak"):
        text = p.get("text") or ""
        if p.get("action") != "speak":
            out.append(("R_BEAT", "this step must be someone speaking"))
        elif not re.fullmatch(SPOKEN_PATTERN, text) or not MIN_SPOKEN_WORDS <= len(text.split()) <= MAX_SPOKEN_WORDS:
            out.append(("R_BEAT", f"say one plain sentence of {MIN_SPOKEN_WORDS} to {MAX_SPOKEN_WORDS} words"))
    if step.get("mark"):
        pat = DIGITS_PATTERN if step["mark"] == "digits" else WORDS_PATTERN
        if p.get("action") != "mark":
            out.append(("R_BEAT", "this step must be a mark"))
        elif not re.fullmatch(pat, p.get("text") or ""):
            out.append(("R_BEAT", f"mark text {p.get('text')!r} does not match {pat}"))
        elif step["mark"] == "digits" and trivial_code(p["text"]):
            out.append(("R_BEAT", f"{p['text']} is too obvious; choose digits that mean something to the writer"))
    return out


def trivial_code(code: str) -> bool:
    d = [int(ch) for ch in code]
    steps = {d[i + 1] - d[i] for i in range(3)}
    return len(set(d)) == 1 or steps in ({1}, {-1}) or code in {"1212", "6969", "1111", "0000"}


def parse(text: str) -> dict | None:
    """Model JSON -> proposal. The model-facing field 'recipient' is the engine's 'to'."""
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    if "recipient" in d:
        d["to"] = d.pop("recipient")
    return d


def resolve_fallback(state: dict, fb: dict, rng: random.Random, mark_kind: str | None) -> dict:
    p = {"to": "", "text": "", "summary": ""} | dict(fb)
    if p["actor"].startswith("$holder:"):
        p["actor"] = state["objects"][p["actor"].split(":", 1)[1]]["holder"] or "nobody"
    if p.get("text") == "$mark":
        if mark_kind == "digits":
            p["text"] = f"{rng.randrange(10000):04d}"
            while trivial_code(p["text"]):
                p["text"] = f"{rng.randrange(10000):04d}"
        else:
            p["text"] = rng.choice(FALLBACK_WORDS)
    if p.get("text") == "$speech":
        p["text"] = rng.choice(FALLBACK_SPOKEN)
    if p.get("target") == "$here":
        p["target"] = state["chars"].get(p["actor"], {}).get("location", "nowhere")
    return p


def simulate_shadow(timeline: Timeline, bid: str, spec_key: str, model, seed: int, log: RunLog,
                    specs: dict | None = None) -> list:
    """Run a shadow to its budget, then freeze it. Returns the committed events."""
    spec = (specs or load_shadows())[spec_key]
    rng = random.Random(f"{seed}:{bid}:fallback")
    timeline.author(bid, spec["setup"], spec["setup_summary"], request_id=f"{bid}:setup")
    committed = []
    for i, step in enumerate(spec["steps"]):
        rid = f"{bid}:step{i}"
        feedback = None
        event = None
        kind = step.get("mark") or ("spoken" if step.get("speak") else None)
        flavor = random.Random(f"{seed}:{bid}:{i}:meaning").choice(MEANINGS[kind]) if kind else None
        for attempt in range(MAX_ATTEMPTS):
            state = timeline.state(bid)
            msgs = build_messages(timeline, bid, spec, step, feedback, flavor)
            schema = step_schema(state, step)
            call_seed = seed * 1000 + (0 if bid == "B" else 500) + i * 10 + attempt
            t0 = time.perf_counter()
            try:
                reply = model.generate(msgs, schema, seed=call_seed)
                raw, meta = reply.text, reply.to_dict()
            except Exception as exc:  # network or server failure counts as a failed attempt
                raw, meta = "", {"error": repr(exc), "latency_s": time.perf_counter() - t0}
            proposal = parse(raw)
            rec = dict(kind="proposal", branch=bid, step=i, attempt=attempt, request_id=rid,
                       prompt_hash=prompt_hash(msgs, schema), call_seed=call_seed, raw=raw, meta=meta,
                       proposal=proposal)
            if proposal is None:
                rec["decision"] = {"accepted": False, "violations": [{"rule": "R_SCHEMA", "detail": "not a JSON object"}]}
                feedback = "the reply was not a JSON object"
                log.add(**rec)
                continue
            decision = timeline.check(bid, proposal)
            beat = beat_violations(step, proposal, decision, state)
            violations = beat + decision.violations
            rec["decision"] = {"accepted": not violations,
                               "violations": [{"rule": r, "detail": d} for r, d in violations],
                               "preconditions": decision.preconditions}
            if violations:
                feedback = "; ".join(f"{r}: {d}" for r, d in violations)
                log.add(**rec)
                continue
            event = timeline.commit(bid, proposal, source="model", request_id=rid)
            rec["event_id"] = event.event_id
            log.add(**rec)
            break
        if event is None:
            fallbacks = list(step.get("fallback", []))
            if step.get("mark"):
                # Last resort: whoever holds any markable object marks it, so every shadow
                # still ends with a post-fork fact that can cross.
                st = timeline.state(bid)
                fallbacks += [{"actor": o["holder"], "action": "mark", "target": oid, "text": "$mark"}
                              for oid, o in sorted(st["objects"].items())
                              if o["markable"] and o["holder"] and st["chars"][o["holder"]]["status"] not in INACTIVE]
            if step.get("speak"):
                # Last resort: someone still standing says something aloud where they are.
                st = timeline.state(bid)
                fallbacks += [{"actor": cid, "action": "speak", "target": "$here", "text": "$speech"}
                              for cid, c in st["chars"].items() if c["status"] not in INACTIVE]
            for fb in fallbacks:
                p = resolve_fallback(timeline.state(bid), fb, rng, step.get("mark"))
                d = timeline.check(bid, p)
                if d.accepted and not beat_violations(step, p, d, timeline.state(bid)):
                    p["summary"] = p["summary"] or ""
                    event = timeline.commit(bid, p, source="fallback", request_id=rid)
                    log.add(kind="fallback", branch=bid, step=i, request_id=rid, proposal=p,
                            event_id=event.event_id)
                    break
            else:
                log.add(kind="skipped", branch=bid, step=i, request_id=rid)
        if event is not None:
            committed.append(event)
    timeline.freeze(bid)
    return committed


def pick_origin(timeline: Timeline, bid: str, action: str = "mark"):
    """The shadow event that crosses: the latest event of the given action (a mark for B,
    words spoken for D), else the latest simulated event."""
    events = [e for e in timeline.branch(bid).events if e.source in ("model", "fallback")]
    chosen = [e for e in events if e.action == action]
    return (chosen or events)[-1] if (chosen or events) else None


def crossing_action(spec: dict) -> str:
    return "speak" if spec["steps"][-1].get("speak") else "mark"
