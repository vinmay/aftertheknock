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
from .models import TransportError, prompt_hash
from .state import apply_delta, initial_state, is_plural, location_of

SYSTEM = ("You add one sentence of atmosphere to a quiet, literary horror story, in second person, "
          "present tense. Concrete senses only: sound, cold, light, smell, touch. No dialogue. "
          "Keep each sense literal: light shows only what can be seen; smells and sounds are sensed directly, "
          "never 'revealed' by light. Stay on the surfaces of where you are. No people, figures, faces or body "
          "parts, and nothing that suggests someone else is present.")

CHAR_NAMES = {"mara": "Mara", "teo": "Teo"}
FORBIDDEN_WORDS = ("timeline", "shadow", "branch", "json", "simulation")
# Cliches the story never uses, in shadow text or narration.
BANNED_PHRASES = ("help me", "get out", "you're next", "youre next", "it's not over", "its not over",
                  "don't look back", "dont look back", "i'm watching you", "im watching you",
                  "watch out", "too late", "behind you", "run while you can", "run", "you will die",
                  "die", "kill", "blood")
MAX_COLOR = 160
# Stock images the small model leans on, and body details: never in the atmosphere sentence.
NARRATION_BANNED = ("ozone", "wet stone", "damp stone", "bare feet", "skin", "chill", "viscous")
# Body parts, footwear, and anything that implies someone else is there, matched as whole words (a plural
# too): "face" never catches "surface".
BODY_WORDS = ("face", "hair", "neck", "cheek", "lip", "mouth", "chest", "spine", "shoulder", "arm",
              "hand", "finger", "palm", "wrist", "foot", "feet", "toe", "ankle", "shoe", "sock",
              "teeth", "tooth", "eye", "figure", "someone", "somebody", "silhouette", "shape", "breath", "breathing")
# Light that "reveals" a smell or a sound mixes the senses ("a flash of white light reveals the smell of ...").
LIGHT_WORDS = r"\b(light|lights|flash|flashes|lightning|glow|glows|glowing)\b"
# Light and a smell or sound in one sentence mixes the senses whatever the verb ("exposes", "hits", ...).
LIGHT_ANY = (r"\b(light|lights|flash|flashes|lightning|glow|glows|glowing|gleam|gleams|gleaming|"
             r"flicker|flickers|flickering|beam|beams|ray|rays)\b")
SMELL_SOUND = (r"\b(smell|smells|scent|scents|odou?r|odou?rs|stench|sound|sounds|noise|noises|drumming|"
               r"hum|hums|humming|whisper|whispers|whispering|scratching)\b")
REVEAL = r"\breveal(s|ed|ing)?\b"
UNSEEN = (r"\b(smell|smells|scent|scents|odou?r|odou?rs|stench|sound|sounds|drumming|noise|noises|hum|hums|humming|"
          r"whisper|whispers|whispering|hitting|dripping|tapping|scraping|clicking)\b")
# Where you are, in plain words, and the surfaces that can't be there.
LOCATION = {"apt": "your apartment", "stairwell": "the stairwell", "roof": "the roof, outdoors", "basement": "the basement",
            "unit07": "the basement, at storage unit 07", "hall": "the hallway"}
OUTDOORS = {"roof"}
WRONG_SURFACES = {
    "outdoors": ("concrete", "floor", "floorboards", "carpet", "ceiling", "linoleum", "tiles",
                 "shingles", "shingle", "slates"),           # the roof is tar
    "indoors": ("tar", "sky", "rooftop", "parapet"),
}
REPEAT_OVERLAP = 0.5        # share of content words two color sentences may have in common
STOP = {"the", "a", "an", "and", "of", "to", "in", "on", "at", "your", "you", "it", "its", "is", "are", "as",
        "with", "from", "into", "then", "through", "against", "over", "by", "for", "like", "that", "this"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'").replace("‘", "'")).strip()


def banned_in(text: str) -> list[str]:
    t = normalize(text)
    return [p for p in BANNED_PHRASES if re.search(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", t)]


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z']+", normalize(text)) if w not in STOP}


def near_duplicate(text: str, previous: str | None) -> bool:
    """Too close to the previous crossing's sentence: most of the smaller one's content words recur."""
    if not previous:
        return False
    a, b = _words(text), _words(previous)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= REPEAT_OVERLAP


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


def template_parts(m: dict, templates: dict, pocket: str, style: str = "paper") -> dict:
    """Hand-written lines for this crossing, with the committed facts slotted in."""
    holder = CHAR_NAMES.get(m["holder"] or "", "")
    slots = {"pocket": pocket, "place": m["place"], "holder": holder, "it": "them" if is_plural(pocket) else "it"}
    situation = m["situation"]
    t = (templates.get(f"mark_{m['object']}_{situation}") or templates.get(f"mark_{m['object']}_{situation.split('_')[0]}")
         or templates.get(f"mark_{m['object']}") or templates["mark_other"])
    writers = templates.get("writer" if style == "paper" else f"writer_{style}", templates["writer"])
    who = writers.get(m["writer"], writers["mara"])
    return {"object": m["object"], "situation": m["situation"], "before": _fill(t["before"], slots),
            "inscription": m["text"], "after": _fill(t["after"], slots), "who": _fill(who, slots)}


def build_messages(m: dict, avoid: str | None = None) -> list[dict]:
    with_you = ", ".join(CHAR_NAMES[c] for c in m["with_you"] if c in CHAR_NAMES) or "no one"
    where = LOCATION.get(m["here"], m["place"])
    weather = " It is raining hard." if m["here"] in OUTDOORS else ""
    user = (f"Where you are: {where}.{weather} With you: {with_you}. The power is out; the only light is the "
            f"storm's. Something impossible has just happened, and you have gone very still.\n"
            f"Write ONE short sentence (under 20 words) about what you sense around you. "
            f"No names, no numbers, no quotes, no words in ALL CAPS, and don't explain anything. "
            f"Nothing about your body, skin, hands or feet, and nothing about shoes. "
            f"Never use these words: {', '.join(NARRATION_BANNED + BODY_WORDS)}.")
    if avoid:
        user += f"\nDo not repeat the last one, or its images: {avoid}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def check(text: str, here: str | None = None) -> list[str]:
    """Rule checks on the color sentence (`here`: where the player is). Returns the problems found."""
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
    low = normalize(t)
    stock = [w for w in NARRATION_BANNED if re.search(rf"\b{re.escape(w)}", low)]
    stock += [w for w in BODY_WORDS if re.search(rf"\b{w}s?\b", low)]
    if stock:
        problems.append(f"C_STOCK: {stock}")
    if re.search(LIGHT_WORDS, low) and re.search(REVEAL, low) and re.search(UNSEEN, low):
        problems.append("C_SENSE: light can't reveal a smell or a sound")
    elif re.search(LIGHT_ANY, low) and re.search(SMELL_SOUND, low):
        problems.append("C_SENSE: light and a smell or sound in one sentence")
    if here:
        wrong = [w for w in WRONG_SURFACES["outdoors" if here in OUTDOORS else "indoors"] if re.search(rf"\b{w}\b", low)]
        if wrong:
            problems.append(f"C_PLACE: {wrong} doesn't fit {LOCATION.get(here, here)}")
    return problems


def compose(parts: dict, color: str | None) -> str:
    lines = [parts["before"], f"“{parts['inscription']}”", f"{parts['after']} {parts['who']}".strip()]
    if color:
        lines.append(color)
    if parts.get("echo"):                   # last: a line about the player's own choice, never a shadow fact
        lines.append(parts["echo"])
    return "\n\n".join(lines)


def narrate_crossing(timeline: Timeline, x: Intrusion, model, pocket: str, seed: int, log=None,
                     templates: dict | None = None, style: str = "paper", avoid: str | None = None) -> dict:
    view = canonical_view(timeline, x.target_branch)
    oid = _crossing_fact([f for f in view["released"] if f in x.released])[0]
    m = moment(view, x, seen_before(timeline, x, oid))
    parts = template_parts(m, templates, pocket, style)
    msgs = build_messages(m, avoid)
    raw, color = None, None
    for attempt in range(2):
        transport = None
        try:
            reply = model.generate(msgs, None, seed=seed * 1000 + 900 + attempt, max_tokens=60)
            model_text, meta = reply.text.strip(), reply.to_dict()
        except TransportError as exc:
            model_text, meta, transport = "", {"error": exc.reason}, exc.reason
        except Exception as exc:
            model_text, meta = "", {"error": repr(exc)}
        problems = check(model_text, m["here"]) if transport is None else [f"TRANSPORT: {transport}"]
        repeat = transport is None and not problems and near_duplicate(model_text, avoid)
        if repeat:
            problems = ["C_REPEAT: too close to the last crossing's sentence"]
        if raw is None:
            raw = {"text": model_text, "problems": problems}
        if log is not None:
            rec = dict(kind="narration", branch=x.target_branch, intrusion=x.intrusion_id, attempt=attempt,
                       prompt_hash=prompt_hash(msgs, None), raw=model_text, text=model_text,
                       problems=problems, meta=meta)
            if transport is not None:
                rec["transport"] = transport
            log.add(**rec)
        if transport is not None or repeat:
            break                                   # provider down, or a repeat: hand-written lines only, no extra call
        if not problems:
            color = model_text
            break
    if color is None and log is not None:
        log.add(kind="narration_fallback", branch=x.target_branch, intrusion=x.intrusion_id,
                text="(no color sentence; hand-written lines only)")
    parts["color"] = color
    return {"text": compose(parts, color), "parts": parts, "color": color,
            "source": "template+model" if color else "template", "raw": raw, "moment": m, "messages": msgs}
