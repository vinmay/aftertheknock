"""Deterministic one-line descriptions of committed events, from their fields only.

The model's own summary is kept and shown, labeled, but this is what the map treats as true.
"""

from __future__ import annotations

from .narrator import CHAR_NAMES


def who(cid: str, shadow: bool) -> str:
    if cid == "player":
        return "the other you" if shadow else "you"
    return CHAR_NAMES.get(cid, cid)


def display_name(oid: str, pocket: str, form: str = "object") -> str:
    """The only way an object id becomes player-facing text.

    form: "object" (the bus ticket), "subject" (Your bus ticket / The brass key), "on" (the key's tag).
    """
    if oid == "pocket":
        return {"subject": f"Your {pocket}", "on": f"the {pocket}"}.get(form, f"the {pocket}")
    if oid == "key07":
        return {"subject": "The brass key", "on": "the key's paper tag"}.get(form, "the brass key")
    return oid.replace("_", " ")


def what(state: dict, eid: str, pocket: str) -> str:
    if eid in ("pocket", "key07"):
        return display_name(eid, pocket)
    for kind in ("chars", "places"):
        if eid in state[kind]:
            return CHAR_NAMES.get(eid) or state[kind][eid]["name"]
    return eid


def describe(e: dict, state: dict, pocket: str, style_for=None) -> str:
    """style_for(object_id) -> "paper" | "scratched" | "unknown" decides the verb for marks."""
    shadow = e["branch"] in ("B", "D")
    a = who(e["actor"], shadow)
    a = a[0].upper() + a[1:]
    t = what(state, e["target"], pocket)
    if e["target"] == "player":
        t = who("player", shadow)
    act = e["action"]
    if act == "story":
        return e["summary"]
    if act == "manifest":
        bits = []
        for op, p, v in e["released"]:
            if p.endswith(".marks"):
                bits.append(f'"{v}" appears on {what(state, p.split(".")[1], pocket)}')
        return "; ".join(bits) + "."
    if act == "move":
        return f"{a} went to {t}."
    if act == "speak":
        if e["target"] in state["places"]:
            return f'{a} said aloud: "{e["text"]}"'
        return f'{a} said to {t}: "{e["text"]}"'
    if act == "give":
        return f"{a} gave {t} to {who(e['to'], shadow)}."
    if act == "take":
        return f"{a} took {t}."
    if act == "mark":
        style = style_for(e["target"]) if style_for else "paper"
        if style == "scratched":
            return f'{a} scratched "{e["text"]}" into {t}.'
        if style == "unknown":
            return f'{a} marked {t} with "{e["text"]}".'
        return f'{a} wrote "{e["text"]}" on {t}.'
    if act == "open":
        return f"{a} unlocked {t}."
    if act == "hide":
        return f"{a} hid {t}."
    if act == "discover":
        return f"{a} found {t}."
    if act == "harm":
        return f"{a} hurt {t}."
    return f"{a} {act} {t}."
