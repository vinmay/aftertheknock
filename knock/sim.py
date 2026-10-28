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
from .engine import crossed_objects
from .models import TransportError
from .narrator import banned_in
from .validator import ACTIONS, SPEECH_SCHEMA_MAX

MAX_ATTEMPTS = 3
WORDS_PATTERN = "^[A-Z][A-Z' ]{2,22}$"
DIGITS_PATTERN = "^[0-9]{4}$"
# Seeded hints that steer what a mark means, so the model's digits and words vary by seed.
MEANINGS = {
    "digits": ["the year someone disappeared", "a date written as month and day", "an apartment number in another building",
               "the age someone will never reach", "the last four digits of a phone number nobody answers",
               "a bus route that stopped running", "the number of days someone waited", "a locker combination"],
    "words": ["a name", "a place in this building", "an instruction", "a name and a place",
              "an instruction about the stairs", "a floor and a door", "someone's first name, twice",
              "a short instruction about what to do with the key"],
}
# A words mark is composed only from these: the building, its people, plain instructions. The shape
# check alone let a small model write word salad ("FLOOR COOKIE C", "BASSMENT STATIC").
MARK_WORDS = """
MARA TEO ELENA ELIAS NORA RUTH VICTOR
ROOF ROOFTOP BASEMENT STAIRWELL STAIRS STAIR STEP STEPS HALL DOOR DOORS ROOM UNIT CAGE FLOOR LANDING
WINDOW LIGHT LIGHTS KEY TAG LOCK PADLOCK STORM RAIN WATER PIPES COLD SPOT DARK NIGHT NAME BELL CLOCK
CHAIR APARTMENT BUILDING ELEVATOR LOBBY
COUNT FIND LEAVE ASK OPEN CLOSE KEEP WAIT STAY TRUST LISTEN GO COME TURN HIDE TAKE GIVE BRING KNOCK
ANSWER CALL REMEMBER FORGET WATCH LOOK SAY TELL LET SIT SLEEP
ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH
EIGHTH NINTH TENTH LAST NEXT
THE A AN TO FOR OF ON IN UP DOWN AT BY FROM WITH AND OR NOT NO NEVER ALWAYS ONLY AGAIN TWICE ONCE
NOW HERE THERE BACK UNLOCKED LOCKED SHUT LEFT RIGHT SAME OTHER ALONE INSIDE OUTSIDE BELOW ABOVE STILL
DON'T IT HER HIM ME YOU US THEM SHE HE I WE IS WAS ARE MY YOUR
""".split()
MARK_WORD_SET = set(MARK_WORDS)
DANGLING = {"THE", "A", "AN", "TO", "FOR", "OF", "ON", "IN", "AT", "BY", "FROM", "WITH", "AND", "OR", "MY", "YOUR", "NOT"}


def words_problem(text: str) -> str | None:
    """Why a words mark is not a readable phrase from this night, or None."""
    words = text.split(" ")
    if "" in words:
        return "use single spaces between whole words"
    if not 1 <= len(words) <= 4:
        return "write one to four words"
    unknown = [w for w in words if w not in MARK_WORD_SET]
    if unknown:
        return f"{' '.join(unknown)} is not one of the allowed words; use only the listed words"
    if words[-1] in DANGLING:
        return f"the phrase cannot end on {words[-1]}"
    return None


FALLBACK_WORDS = ["ASK FOR ELENA", "SIXTH STEP DOWN", "LEAVE IT UNLOCKED", "ROOM SEVEN AGAIN", "COUNT THE STAIRS"]
# Shown to the model; the full list (knock.narrator.BANNED_PHRASES) is enforced at validation.
BANNED_IN_PROMPT = ["HELP ME", "GET OUT", "YOU'RE NEXT", "RUN", "BEHIND YOU", "IT'S NOT OVER", "DON'T LOOK BACK"]

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
        "Spoken lines: one complete sentence, under 45 characters.",
        "Rules: act only where you are; give, mark or hide only what you hold; move one step to an "
        "adjacent place; a locked place needs its key.",
        f"Next event: {step['hint']}" + (f" What is written is {flavor}." if flavor else ""),
        *([f"Write it in one to four of these words only, in capitals: {' '.join(MARK_WORDS)}."]
          if step.get("mark") == "words" else []),
        "Never write these: " + ", ".join(BANNED_IN_PROMPT) + ". Write something specific to this night instead.",
        "Fields: summary (what happens), actor, action, target, recipient (person receiving, for give "
        "only, else \"\"), text (words spoken or written, else \"\").",
    ]
    if feedback:
        parts.append(f"Your last proposal was rejected: {feedback}. Do not repeat it. "
                     "Propose a different event that is possible right now.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def step_schema(state: dict, step: dict, crossed: set[str] = frozenset()) -> dict:
    chars = [c for c, v in state["chars"].items() if v["status"] not in INACTIVE]
    ids = list(state["chars"]) + list(state["objects"]) + list(state["places"])
    props = {
        "summary": {"type": "string", "maxLength": 160},
        "actor": {"enum": chars},
        "action": {"enum": list(step.get("actions", ACTIONS))},
        "target": {"enum": ids},
        "recipient": {"enum": [""] + list(state["chars"])},
        "text": {"type": "string", "maxLength": SPEECH_SCHEMA_MAX},
    }
    if step.get("mark"):
        held = {o: v["holder"] for o, v in state["objects"].items()
                if v["markable"] and v["holder"] in chars and o not in crossed}
        prefer = step.get("prefer")
        if prefer in held:
            held = {prefer: held[prefer]}
        props["actor"] = {"enum": sorted(set(held.values())) or chars}
        props["action"] = {"enum": ["mark"]}
        props["target"] = {"enum": sorted(held) or [o for o, v in state["objects"].items()
                                                      if v["markable"] and o not in crossed]}
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


def beat_violations(step: dict, p: dict, decision=None, state: dict | None = None,
                    crossed: set[str] = frozenset()) -> list[tuple[str, str]]:
    """Story constraints for this step, separate from world rules."""
    out = []
    if decision is not None and decision.accepted and state is not None:
        after = apply_delta(state, decision.delta)
        unmet = goal_unmet(step, after)
        if unmet:
            out.append(("R_BEAT", unmet))
        if not step.get("mark") and not markable_in_hand(after, crossed):
            out.append(("R_BEAT", "someone must still be holding an object that can be written on"))
    if p.get("action") not in step.get("actions", ACTIONS) and not step.get("mark"):
        out.append(("R_BEAT", f"action {p.get('action')!r} not allowed for this step"))
    banned = banned_in(p.get("text") or "")
    if banned:
        out.append(("R_BEAT", f"banned phrase {banned[0]!r}; write something specific to this night"))
    if step.get("mark") and p.get("target") in crossed:
        out.append(("R_BEAT", f"{p.get('target')} already carried a crossing; mark a different object"))
    if step.get("mark"):
        pat = DIGITS_PATTERN if step["mark"] == "digits" else WORDS_PATTERN
        if p.get("action") != "mark":
            out.append(("R_BEAT", "this step must be a mark"))
        elif not re.fullmatch(pat, p.get("text") or ""):
            out.append(("R_BEAT", f"mark text {p.get('text')!r} does not match {pat}"))
        elif step["mark"] == "words" and words_problem(p["text"]):
            out.append(("R_BEAT", f"mark text {p['text']!r}: {words_problem(p['text'])}"))
        elif step["mark"] == "digits" and trivial_code(p["text"]):
            out.append(("R_BEAT", f"{p['text']} is too obvious; choose digits that mean something to the writer"))
    return out


def markable_in_hand(state: dict, crossed: set[str] = frozenset()) -> bool:
    """The shadow's final mark needs an active character holding an object that hasn't crossed yet."""
    return any(o["markable"] and oid not in crossed and o["holder"]
               and state["chars"][o["holder"]]["status"] not in INACTIVE
               for oid, o in state["objects"].items())


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
    # Objects that already carried a crossing into the player's night; a later crossing must differ.
    crossed = set().union(*(crossed_objects(x.post_fork) for x in timeline.intrusions)) if timeline.intrusions else set()
    timeline.author(bid, spec["setup"], spec["setup_summary"], request_id=f"{bid}:setup")
    committed = []
    # Hosted models get a cooperative wall-clock budget for the whole shadow; once it is spent the
    # remaining steps resolve to validated authored fallbacks. Local models (Ollama) are unchanged.
    budget = getattr(model, "shadow_budget_s", None)
    deadline = time.monotonic() + budget if budget else None
    retries = getattr(model, "transport_retries", None)
    forced = getattr(model, "forced_fallback", set())      # replay: steps the original resolved by budget
    for i, step in enumerate(spec["steps"]):
        rid = f"{bid}:step{i}"
        feedback = None
        event = None
        reason = "the model's proposals were rejected"
        transport_failures = 0
        use_model = (bid, i) not in forced and not (deadline and time.monotonic() >= deadline)
        if not use_model:
            reason = "budget: the shadow's time budget was spent"
        flavor = (random.Random(f"{seed}:{bid}:{i}:meaning").choice(MEANINGS[step["mark"]])
                  if step.get("mark") else None)
        for attempt in range(MAX_ATTEMPTS if use_model else 0):
            if deadline and time.monotonic() >= deadline:
                reason = "budget: the shadow's time budget was spent"
                break
            state = timeline.state(bid)
            msgs = build_messages(timeline, bid, spec, step, feedback, flavor)
            schema = step_schema(state, step, crossed)
            call_seed = seed * 1000 + (0 if bid == "B" else 500) + i * 10 + attempt
            t0 = time.perf_counter()
            transport = None
            try:
                kw = {"deadline": deadline} if deadline and getattr(model, "supports_deadline", False) else {}
                reply = model.generate(msgs, schema, seed=call_seed, **kw)
                raw, meta = reply.text, reply.to_dict()
            except TransportError as exc:          # the provider failed, not the proposal
                raw, meta, transport = "", {"error": exc.reason, "latency_s": time.perf_counter() - t0}, exc.reason
            except Exception as exc:  # local model or other failure counts as a failed attempt
                raw, meta = "", {"error": repr(exc), "latency_s": time.perf_counter() - t0}
            proposal = parse(raw)
            rec = dict(kind="proposal", branch=bid, step=i, attempt=attempt, request_id=rid,
                       prompt_hash=prompt_hash(msgs, schema), call_seed=call_seed, raw=raw, meta=meta,
                       proposal=proposal)
            if transport is not None:
                rec["transport"] = transport
                rec["decision"] = {"accepted": False, "violations": [{"rule": "TRANSPORT", "detail": transport}]}
                log.add(**rec)
                transport_failures += 1
                if retries is None or transport_failures > retries:
                    reason = f"transport: {transport}"
                    break
                continue
            if proposal is None:
                rec["decision"] = {"accepted": False, "violations": [{"rule": "R_SCHEMA", "detail": "not a JSON object"}]}
                feedback = "the reply was not a JSON object"
                log.add(**rec)
                continue
            decision = timeline.check(bid, proposal)
            beat = beat_violations(step, proposal, decision, state, crossed)
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
                              if o["markable"] and o["holder"] and oid not in crossed
                              and st["chars"][o["holder"]]["status"] not in INACTIVE]
            for fb in fallbacks:
                p = resolve_fallback(timeline.state(bid), fb, rng, step.get("mark"))
                d = timeline.check(bid, p)
                if d.accepted and not beat_violations(step, p, d, timeline.state(bid), crossed):
                    p["summary"] = p["summary"] or ""
                    event = timeline.commit(bid, p, source="fallback", request_id=rid)
                    log.add(kind="fallback", branch=bid, step=i, request_id=rid, proposal=p,
                            event_id=event.event_id, reason=reason)
                    break
            else:
                log.add(kind="skipped", branch=bid, step=i, request_id=rid)
        if event is not None:
            committed.append(event)
    timeline.freeze(bid)
    return committed


def pick_origin(timeline: Timeline, bid: str, action: str = "mark"):
    """The shadow event that crosses: the latest mark, else the latest simulated event."""
    events = [e for e in timeline.branch(bid).events if e.source in ("model", "fallback")]
    chosen = [e for e in events if e.action == action]
    return (chosen or events)[-1] if (chosen or events) else None

