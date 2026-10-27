"""The blind narrator.

It sees only the canonical timeline's state plus facts released by committed
intrusions (`canonical_view`). It never receives shadow events or shadow state.
Its prose is checked against those facts; on failure it regenerates once and
then falls back to hand-written text.
"""

from __future__ import annotations

import re

from .engine import Intrusion, Timeline
from .models import prompt_hash

SYSTEM = ("You narrate one moment of a quiet, literary horror story in second person, present tense. "
          "Write two or three short sentences with one concrete physical detail (the ink, your hands, "
          "the light, a sound). Use only the facts given. Never explain what is happening, and "
          "never mention timelines.")

CHAR_NAMES = {"mara": "Mara", "teo": "Teo"}
FORBIDDEN_WORDS = ("timeline", "shadow", "branch", "json", "simulation")
MAX_LEN = 600


def canonical_view(timeline: Timeline, bid: str) -> dict:
    """Everything the narrator may know: canonical state and released facts. Nothing else."""
    if timeline.branch(bid).kind == "shadow":
        raise ValueError("the narrator only narrates canonical timelines")
    return {"branch": bid, "state": timeline.state(bid), "released": timeline.released_facts(bid)}


def _mark_fact(released: list) -> tuple[str, str] | None:
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


def moment(view: dict, x: Intrusion) -> dict:
    """The facts of one crossing, as the canonical side is allowed to see them."""
    s = view["state"]
    released = [f for f in view["released"] if f in x.released]
    oid, text = _mark_fact(released)
    writer = _writer(released)
    obj = s["objects"][oid]
    here = s["chars"]["player"]["location"]
    with_you = [c for c, v in s["chars"].items()
                if c != "player" and v["location"] == here and v["status"] not in ("dead", "departed")]
    if obj["holder"] == "player":
        where = "in your hand"
    elif obj["holder"]:
        where = f"in {s['chars'][obj['holder']]['name']}'s hand"
    else:
        where = f"lying in {s['places'][obj['location']]['name']}"
    return {"object": oid, "object_name": obj["name"], "text": text, "writer": writer, "where": where,
            "place": s["places"][here]["name"], "with_you": with_you,
            "allowed_names": sorted({CHAR_NAMES[c] for c in with_you if c in CHAR_NAMES}
                                    | ({CHAR_NAMES[writer]} if writer in CHAR_NAMES else set()))}


def writer_phrase(writer: str | None) -> str:
    if writer == "player":
        return "the handwriting is your own, but you never wrote it"
    return f"the handwriting is {CHAR_NAMES.get(writer, 'someone')}'s"


def build_messages(m: dict, pocket: str) -> list[dict]:
    noun = "the key's paper tag" if m["object"] == "key07" else f"the {pocket}"
    with_you = ", ".join(CHAR_NAMES[c] for c in m["with_you"] if c in CHAR_NAMES) or "no one"
    user = "\n".join([
        f"Where you are: {m['place']}. With you: {with_you}. The power is out; the only light is the storm's.",
        f"What changed: {noun} ({m['where']}) now has \"{m['text']}\" written on it. It was not there a moment ago.",
        f"Let the reader sense, without saying it outright, that {writer_phrase(m['writer'])}.",
        f"Write the moment you notice it, in normal sentences. Name {noun} in your first sentence. "
        f"Quote the writing exactly as \"{m['text']}\". "
        "Apart from the writing itself, use no numbers and no words in ALL CAPS.",
    ])
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def restore(text: str, m: dict) -> str:
    """If the inscription appears with different letter case, put back its exact form."""
    if m["text"] in text:
        return text
    return re.sub(re.escape(m["text"]), m["text"], text, count=1, flags=re.IGNORECASE)


def check(text: str, m: dict, pocket: str) -> list[str]:
    """Rule checks on the critical claims. Returns the problems found."""
    problems = []
    rest = re.sub(re.escape(m["text"]), " ", text, flags=re.IGNORECASE) if m["text"] else text
    if not text or len(text) > MAX_LEN or len(text) < 40:
        problems.append("C_LENGTH")
    if m["text"] not in text:
        problems.append("C_TEXT: the written value is missing or altered")
    numbers = set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", m["text"])) - {"07"}
    if numbers:
        problems.append(f"C_NUMBERS: invented numbers {sorted(numbers)}")
    for name in CHAR_NAMES.values():
        if re.search(rf"\b{name}\b", text) and name not in m["allowed_names"]:
            problems.append(f"C_NAMES: {name} is not here and did not write it")
    words = [w for w in re.findall(r"[a-z]{3,}", pocket.lower())]
    obj_words = ("key", "tag") if m["object"] == "key07" else tuple(words) or ("pocket",)
    if not any(w in text.lower() for w in obj_words):
        problems.append("C_OBJECT: the object is not mentioned")
    if any(w in rest.lower() for w in FORBIDDEN_WORDS):
        problems.append("C_META: mentions the machinery")
    caps = set(re.findall(r"\b[A-Z]{3,}\b", rest)) - {"BASEMENT"}
    if caps:
        problems.append(f"C_CAPS: invented capitalized words {sorted(caps)}")
    return problems


def fallback(m: dict, pocket: str) -> str:
    who = {"player": "It's your handwriting. You don't remember writing it.",
           "mara": "It isn't your handwriting.",
           "teo": "It isn't your handwriting. It isn't hers either."}.get(m["writer"], "It isn't your handwriting.")
    if m["object"] == "key07":
        return (f"The paper tag on the brass key, {m['where']}, has \"{m['text']}\" scratched into it, "
                f"the letters still raw. {who}")
    return f"You turn the {pocket} over. \"{m['text']}\" is written across it, the ink still wet. {who}"


def narrate_crossing(timeline: Timeline, x: Intrusion, model, pocket: str, seed: int, log=None) -> dict:
    view = canonical_view(timeline, x.target_branch)
    m = moment(view, x)
    raw = None
    for attempt in range(2):
        msgs = build_messages(m, pocket)
        try:
            reply = model.generate(msgs, None, seed=seed * 1000 + 900 + attempt, max_tokens=160)
            model_text, meta = reply.text.strip(), reply.to_dict()
        except Exception as exc:
            model_text, meta = "", {"error": repr(exc)}
        text = restore(model_text, m)
        problems = check(text, m, pocket)
        if raw is None:
            raw = {"text": model_text, "problems": check(model_text, m, pocket)}
        if log is not None:
            log.add(kind="narration", branch=x.target_branch, intrusion=x.intrusion_id, attempt=attempt,
                    prompt_hash=prompt_hash(msgs, None), raw=model_text, text=text, problems=problems,
                    meta=meta)
        if not problems:
            return {"text": text, "source": "model", "raw": raw, "moment": m, "messages": msgs}
    text = fallback(m, pocket)
    if log is not None:
        log.add(kind="narration_fallback", branch=x.target_branch, intrusion=x.intrusion_id, text=text)
    return {"text": text, "source": "fallback", "raw": raw, "moment": m, "messages": msgs}
