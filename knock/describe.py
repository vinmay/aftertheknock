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
        bits = []
        for op, p, v in e["released"]:
            if p.endswith(".marks"):
                bits.append(f'"{v}" appears on {what(state, p.split(".")[1], pocket)}')
            elif p.endswith(".said"):
                bits.append(f'You hear {who(p.split(".")[1], False) if p.split(".")[1] != "player" else "your own voice"} say "{v}"')
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


PLACE_TEASE = {
    "apt": "Your apartment, with the door open.",
    "hall": "The hall, seen from the wrong side of your door.",
    "teo_apt": "A kitchen you have never been inside.",
    "stairwell": "The stairwell, in the dark.",
    "basement": "The basement. Dry.",
    "unit07": "Inside 07.",
    "roof": "The roof, in the rain.",
}


def teaser(e: dict, state_before: dict) -> str:
    """A diegetic hint about where an intrusion came from, built only from its committed event."""
    where = PLACE_TEASE.get(state_before["chars"][e["actor"]]["location"], "Somewhere else.")
    if e["action"] == "speak":
        tool = "Someone breathing in."
        who = {"player": "Your own voice.", "mara": "Her voice.", "teo": "His voice."}.get(e["actor"], "A voice.")
    else:
        tool = "Something sharp." if e["target"] == "key07" else "A pen."
        who = {"player": "Your own hand.", "mara": "Her hand.", "teo": "His hand."}.get(e["actor"], "A hand.")
    return f"{where} {tool} {who}"
