"""Hosted resilience: transport failures fall back honestly, within time bounds, and replay exactly."""

import json
import random
import socket
import threading
import time

import pytest

from knock import audit
from knock.game import Game, replay, verify_replay
from knock.models import OpenAICompatModel, SpendLedger
from tests._fakehttp import QuietHandler, serve

KEY = "test-key"
TIMEOUT = 0.4      # per call (production: 12 s)
BUDGET = 2.0       # per shadow (production: 25 s)


class Provider(QuietHandler):
    mode = "ok"            # ok | hang | 503 | 429 | die_after
    calls = 0
    die_after = 0
    stop = threading.Event()   # set on teardown, so a hanging reply wakes instead of outliving the test

    def _send(self, code, obj):
        self.reply(code, json.dumps(obj).encode())

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Provider.calls += 1
        mode = Provider.mode
        if mode == "die_after" and Provider.calls > Provider.die_after:
            mode = "503"
        if mode == "hang":
            Provider.stop.wait(2)
            return self._send(200, {"choices": [{"message": {"content": "{}"}}]})
        if mode in ("503", "429"):
            return self._send(int(mode), {"error": "unavailable"})
        if "response_format" in req:
            return self._send(400, {"error": "no structured output"})
        last = req["messages"][-1]["content"]
        rng = random.Random(Provider.calls)
        if "Allowed values" in last:
            allowed = json.loads(last.split("Allowed values: ", 1)[1])
            out = {k: (rng.choice(v) if isinstance(v, list) else "4815" if v == "^[0-9]{4}$"
                       else "COUNT THE STAIRS" if isinstance(v, str) and v.startswith("^[A-Z]") else "Hold still.")
                   for k, v in allowed.items()}
            content = json.dumps(out)
        else:
            content = "The radiator ticks twice, then goes quiet."
        self._send(200, {"choices": [{"message": {"content": content}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 10}})


@pytest.fixture
def provider():
    Provider.calls, Provider.mode, Provider.die_after = 0, "ok", 0
    Provider.stop = threading.Event()
    with serve(Provider, stop=Provider.stop) as url:
        yield url


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                  # nothing listens here: connection refused
    return f"http://127.0.0.1:{port}/v1"


def hosted(url, tmp_path):
    m = OpenAICompatModel(url, "gemma-test", KEY, SpendLedger(str(tmp_path / "s.json"), 10, 0.18, 0.5), timeout=TIMEOUT)
    m.shadow_budget_s = BUDGET
    return m


def play(model, f1="lock", f2="roof", end="open"):
    g = Game("bus ticket", 7, model, background=False)
    want = {"fork1": f1, "fork2": f2, "final": end}
    v = g.view()
    while v["choices"]:
        sc = g.story["scenes"][g.scene]
        w = want.get(sc.get("kind"))
        v = g.choose(next((i for i, c in enumerate(sc["choices"]) if w in (c.get("fork"), c.get("ending"))), 0))
    return g


def check_invariants(g):
    t = g.timeline
    assert g.view()["kind"] == "ending"
    assert len(t.intrusions) == 2
    assert all(not p for _, p in audit.provenance(t))          # every crossing has a committed origin
    assert all(ok for _, ok in audit.post_fork(t))
    assert audit.isolation(t) == []
    rec = json.loads(json.dumps(g.record()))
    assert verify_replay(replay(rec), rec)                      # transport failures replay identically


@pytest.mark.parametrize("mode", ["hang", "503", "429"])
def test_transport_failures_fall_back_within_bounds(provider, tmp_path, mode):
    Provider.mode = mode
    t0 = time.monotonic()
    g = play(hosted(provider, tmp_path))
    elapsed = time.monotonic() - t0
    check_invariants(g)
    # Two shadows, each bounded by its budget plus one in-flight call; narration gives up at once.
    assert elapsed < 2 * (BUDGET + TIMEOUT) + 2 * TIMEOUT + 1.5, elapsed
    fbs = [r for r in g.log.records if r["kind"] == "fallback"]
    assert fbs and all(r["reason"].startswith(("transport", "budget")) for r in fbs)
    props = [r for r in g.log.records if r["kind"] == "proposal"]
    assert props and all(r.get("transport") for r in props)
    # At most one retry per step on transport failures.
    per_step = {}
    for r in props:
        per_step[(r["branch"], r["step"])] = per_step.get((r["branch"], r["step"]), 0) + 1
    assert max(per_step.values()) <= 2


def test_connection_refused(tmp_path):
    t0 = time.monotonic()
    g = play(hosted(closed_port_url(), tmp_path))
    check_invariants(g)
    assert time.monotonic() - t0 < 5
    assert all(r["reason"].startswith("transport: unreachable") for r in g.log.records if r["kind"] == "fallback")


def test_provider_dies_after_fork_1(provider, tmp_path):
    Provider.mode, Provider.die_after = "die_after", 6     # enough for shadow B, then 503s
    g = play(hosted(provider, tmp_path))
    check_invariants(g)
    b = [r for r in g.log.records if r.get("branch") == "B" and r["kind"] in ("proposal", "fallback")]
    d = [r for r in g.log.records if r.get("branch") == "D" and r["kind"] == "fallback"]
    assert any(r["kind"] == "proposal" and r["decision"]["accepted"] for r in b)   # B was generated
    assert d and all(r["reason"].startswith(("transport", "budget")) for r in d)   # D fell back honestly


def test_content_failures_still_get_validator_retries(provider, tmp_path):
    """A healthy provider: no transport records, and normal validator-guided behavior."""
    g = play(hosted(provider, tmp_path))
    check_invariants(g)
    assert not any(r.get("transport") for r in g.log.records)


def test_budget_exceeded_replays_identically(provider, tmp_path):
    Provider.mode = "hang"
    m = hosted(provider, tmp_path)
    m.shadow_budget_s = 0.5                      # spent after the first hanging call
    g = play(m)
    check_invariants(g)
    assert any(r["kind"] == "fallback" and r["reason"].startswith("budget") for r in g.log.records)
