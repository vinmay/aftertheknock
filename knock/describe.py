"""Deterministic one-line descriptions of committed events, from their fields only.

The model's own summary is kept and shown, labeled, but this is what the map treats as true.
"""

from __future__ import annotations

from .narrator import CHAR_NAMES


def who(cid: str, shadow: bool) -> str:
    if cid == "player":
        return "the other you" if shadow else "you"
    return CHAR_NAMES.get(cid, cid)


def what(state: dict, eid: str, pocket: str) -> str:
    if eid == "pocket":
        return f"the {pocket}"
    if eid == "key07":
        return "the brass key"
    for kind in ("chars", "places"):
        if eid in state[kind]:
            return CHAR_NAMES.get(eid) or state[kind][eid]["name"]
    return eid


def describe(e: dict, state: dict, pocket: str) -> str:
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
        return f"Crossing from {e['origin']}: " + "; ".join(
            f'"{v}" appears on {what(state, p.split(".")[1], pocket)}' for op, p, v in e["released"]
            if p.endswith(".marks"))
    if act == "move":
        return f"{a} went to {t}."
    if act == "speak":
        return f'{a} said to {t}: "{e["text"]}"'
    if act == "give":
        return f"{a} gave {t} to {who(e['to'], shadow)}."
    if act == "take":
        return f"{a} took {t}."
    if act == "mark":
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
