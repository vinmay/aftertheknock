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
from .validator import ACTIONS

MAX_ATTEMPTS = 3
WORDS_PATTERN = "^[A-Z][A-Z' ]{2,22}$"
DIGITS_PATTERN = "^[0-9]{4}$"
FALLBACK_WORDS = ["DON'T GO DOWN", "SHE IS STILL HERE", "WE STAYED", "NOT THIS NIGHT", "OPEN IT"]

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


def build_messages(timeline: Timeline, bid: str, spec: dict, step: dict, feedback: str | None) -> list[dict]:
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
        "take/give/mark/hide/discover -> an object id; speak/harm -> a person id. "
        "take = get an object from someone with you; give = hand an object you hold to the recipient; "
        "mark = write text on an object you hold.",
        "Rules: act only where you are; give, mark or hide only what you hold; move one step to an "
        "adjacent place; a locked place needs its key.",
        f"Next event: {step['hint']}",
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
    if p.get("action") not in step.get("actions", ACTIONS) and not step.get("mark"):
        out.append(("R_BEAT", f"action {p.get('action')!r} not allowed for this step"))
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
        for attempt in range(MAX_ATTEMPTS):
            state = timeline.state(bid)
            msgs = build_messages(timeline, bid, spec, step, feedback)
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
            for fb in step.get("fallback", []):
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


def pick_origin(timeline: Timeline, bid: str):
    """The shadow event that crosses: the latest mark, else the latest event with new facts."""
    events = [e for e in timeline.branch(bid).events if e.source in ("model", "fallback")]
    marks = [e for e in events if e.action == "mark"]
    return (marks or events)[-1] if (marks or events) else None
