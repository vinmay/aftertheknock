"""The blind narrator.

A crossing is told with hand-written templates (data/story/story.toml, [crossings])
that slot in the committed facts: the inscription, the object, whose hand. The model
may add one short sentence of atmosphere. It sees only the canonical timeline (where
you are, who is with you) and never the shadow log or the inscription. The sentence
is rule-checked; on failure it is regenerated once, then left out.
"""

from __future__ import annotations

import re

from .engine import Intrusion, Timeline
from .models import prompt_hash
from .state import apply_delta, initial_state, is_plural, location_of

SYSTEM = ("You add one sentence of atmosphere to a quiet, literary horror story, in second person, "
          "present tense. Concrete senses only: sound, cold, light, smell, touch. No dialogue.")

CHAR_NAMES = {"mara": "Mara", "teo": "Teo"}
FORBIDDEN_WORDS = ("timeline", "shadow", "branch", "json", "simulation")
# Cliches the story never uses, in shadow text or narration.
BANNED_PHRASES = ("help me", "get out", "you're next", "youre next", "it's not over", "its not over",
                  "don't look back", "dont look back", "i'm watching you", "im watching you",
                  "watch out", "too late", "behind you", "run while you can", "run", "you will die",
                  "die", "kill", "blood")
MAX_COLOR = 160


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'").replace("‘", "'")).strip()


def banned_in(text: str) -> list[str]:
    t = normalize(text)
    return [p for p in BANNED_PHRASES if re.search(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", t)]


def canonical_view(timeline: Timeline, bid: str) -> dict:
    """Everything the narrator may know: canonical state and released facts. Nothing else."""
    if timeline.branch(bid).kind == "shadow":
        raise ValueError("the narrator only narrates canonical timelines")
    return {"branch": bid, "state": timeline.state(bid), "released": timeline.released_facts(bid)}


def _crossing_fact(released: list) -> tuple[str, str] | None:
    """(object, inscription) of the mark that crossed."""
    for op, path, value in released:
        parts = path.split(".")
        if len(parts) == 3 and parts[0] == "objects" and parts[2] == "marks":
            return parts[1], value
    return None


def _writer(released: list) -> str | None:
    for _, path, value in released:
        if path.startswith("events.") and path.endswith(".actor"):
            return value
    return None


def seen_before(timeline: Timeline, x: Intrusion, oid: str) -> bool:
    """Had the player been in the same place as this object earlier in their own night?

    Uses only the canonical lineage up to (not including) the crossing, so the narrator stays
    blind to the shadow: it knows what you saw, not what happened elsewhere."""
    lineage = timeline.lineage(x.target_branch)
    man = next(i for i, e in enumerate(lineage) if e.event_id == x.manifestation_event_id)
    s = initial_state(None, timeline.pocket_name)
    if location_of(s, oid) == s["chars"]["player"]["location"]:
        return True
    for e in lineage[:man]:
        s = apply_delta(s, e.state_delta)
        if location_of(s, oid) == s["chars"]["player"]["location"]:
            return True
    return False


def moment(view: dict, x: Intrusion, seen: bool = True) -> dict:
    """The facts of one crossing, as the canonical side is allowed to see them."""
    s = view["state"]
    released = [f for f in view["released"] if f in x.released]
    oid, text = _crossing_fact(released)
    writer = _writer(released)
    here = s["chars"]["player"]["location"]
    with_you = [c for c, v in s["chars"].items()
                if c != "player" and v["location"] == here and v["status"] not in ("dead", "departed")]
    obj = s["objects"][oid]
    if obj["holder"] == "player":
        situation = "yours"
    elif obj["holder"] in with_you:
        situation = "held"
    else:
        situation = "arrived_seen" if seen else "arrived_unseen"
    return {"object": oid, "text": text, "writer": writer, "here": here, "place": s["places"][here]["name"],
            "with_you": with_you, "holder": obj["holder"], "situation": situation}


def _fill(t: str, slots: dict) -> str:
    for k, v in slots.items():
        t = t.replace("{" + k + "}", v)
    return t


def template_parts(m: dict, templates: dict, pocket: str) -> dict:
    """Hand-written lines for this crossing, with the committed facts slotted in."""
    holder = CHAR_NAMES.get(m["holder"] or "", "")
    slots = {"pocket": pocket, "place": m["place"], "holder": holder, "it": "them" if is_plural(pocket) else "it"}
    situation = m["situation"]
    t = (templates.get(f"mark_{m['object']}_{situation}") or templates.get(f"mark_{m['object']}_{situation.split('_')[0]}")
         or templates.get(f"mark_{m['object']}") or templates["mark_other"])
    who = templates["writer"].get(m["writer"], templates["writer"]["mara"])
    return {"object": m["object"], "situation": m["situation"], "before": _fill(t["before"], slots),
            "inscription": m["text"], "after": _fill(t["after"], slots), "who": _fill(who, slots)}


def build_messages(m: dict) -> list[dict]:
    with_you = ", ".join(CHAR_NAMES[c] for c in m["with_you"] if c in CHAR_NAMES) or "no one"
    user = (f"Where you are: {m['place']}. With you: {with_you}. The power is out; the only light is the "
            f"storm's. Something impossible has just happened, and you have gone very still.\n"
            f"Write ONE short sentence (under 20 words) about what you sense in the room. "
            f"No names, no numbers, no quotes, no words in ALL CAPS, and don't explain anything.")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def check(text: str) -> list[str]:
    """Rule checks on the color sentence. Returns the problems found."""
    problems = []
    t = text.strip()
    if not 10 <= len(t) <= MAX_COLOR:
        problems.append("C_LENGTH")
    if len(re.findall(r"[.!?](?:\s|$)", t)) > 1:
        problems.append("C_SENTENCES: more than one sentence")
    if re.search(r"\d", t):
        problems.append("C_NUMBERS: contains a number")
    if re.search(r"[\"“”]", t):
        problems.append("C_QUOTES: quotes something")
    for name in CHAR_NAMES.values():
        if re.search(rf"\b{name}\b", t):
            problems.append(f"C_NAMES: names {name}")
    if re.search(r"\b[A-Z]{3,}\b", t):
        problems.append("C_CAPS: capitalized words")
    if any(w in t.lower() for w in FORBIDDEN_WORDS):
        problems.append("C_META: mentions the machinery")
    if banned_in(t):
        problems.append(f"C_BANNED: {banned_in(t)}")
    return problems


def compose(parts: dict, color: str | None) -> str:
    lines = [parts["before"], f"“{parts['inscription']}”", f"{parts['after']} {parts['who']}".strip()]
    if color:
        lines.append(color)
    return "\n\n".join(lines)


def narrate_crossing(timeline: Timeline, x: Intrusion, model, pocket: str, seed: int, log=None,
                     templates: dict | None = None) -> dict:
    view = canonical_view(timeline, x.target_branch)
    oid = _crossing_fact([f for f in view["released"] if f in x.released])[0]
    m = moment(view, x, seen_before(timeline, x, oid))
    parts = template_parts(m, templates, pocket)
    msgs = build_messages(m)
    raw, color = None, None
    for attempt in range(2):
        try:
            reply = model.generate(msgs, None, seed=seed * 1000 + 900 + attempt, max_tokens=60)
            model_text, meta = reply.text.strip(), reply.to_dict()
        except Exception as exc:
            model_text, meta = "", {"error": repr(exc)}
        problems = check(model_text)
        if raw is None:
            raw = {"text": model_text, "problems": problems}
        if log is not None:
            log.add(kind="narration", branch=x.target_branch, intrusion=x.intrusion_id, attempt=attempt,
                    prompt_hash=prompt_hash(msgs, None), raw=model_text, text=model_text,
                    problems=problems, meta=meta)
        if not problems:
            color = model_text
            break
    if color is None and log is not None:
        log.add(kind="narration_fallback", branch=x.target_branch, intrusion=x.intrusion_id,
                text="(no color sentence; hand-written lines only)")
    parts["color"] = color
    return {"text": compose(parts, color), "parts": parts, "color": color,
            "source": "template+model" if color else "template", "raw": raw, "moment": m, "messages": msgs}
