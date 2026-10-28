"""Server limits: client IP, rate limits, eviction, hourly spend ceiling."""

import time

from knock import api
from knock.models import SpendCapReached, SpendLedger


class FakeHandler:
    def __init__(self, xff, peer="10.0.0.9"):
        self.headers = {"X-Forwarded-For": xff} if xff is not None else {}
        self.client_address = (peer, 1234)


def test_client_ip_is_last_forwarded_entry():
    assert api.client_ip(FakeHandler("1.2.3.4, 5.6.7.8")) == "5.6.7.8"      # first entry is client-supplied
    assert api.client_ip(FakeHandler("9.9.9.9")) == "9.9.9.9"
    assert api.client_ip(FakeHandler(None)) == "10.0.0.9"


def test_global_and_per_ip_limits(monkeypatch):
    monkeypatch.setattr(api, "_starts", {})
    monkeypatch.setattr(api, "_all_starts", [])
    monkeypatch.setattr(api, "NEW_GAMES_PER_IP_HOUR", 2)
    monkeypatch.setattr(api, "NEW_GAMES_PER_HOUR", 3)
    assert api._rate_ok("a") and api._rate_ok("a")
    assert not api._rate_ok("a")                      # per-IP
    assert api._rate_ok("b")
    assert not api._rate_ok("c")                      # global: 3 already this hour


def test_eviction_by_last_activity(monkeypatch):
    class G:
        def __init__(self, rid):
            self.run_id = rid
    monkeypatch.setattr(api, "_games", {})
    monkeypatch.setattr(api, "_last_seen", {})
    monkeypatch.setattr(api, "MAX_GAMES", 2)
    api._add_game(G("old"))
    time.sleep(0.01)
    api._add_game(G("new"))
    time.sleep(0.01)
    api._get("old")                                   # "old" is active again
    api._add_game(G("third"))
    assert set(api._games) == {"old", "third"}        # the idle one went, not the oldest


def test_hourly_spend_ceiling_holds_without_the_file(tmp_path):
    led = SpendLedger(str(tmp_path / "s.json"), cap_usd=10, price_in_per_m=1000, price_out_per_m=0, hourly_usd=0.002)
    led.add("r", 1, 0)                                # $0.001
    led.check()
    led.add("r", 1, 0)                                # $0.002 this hour
    (tmp_path / "s.json").unlink()                    # losing the file must not reset the ceiling
    try:
        led.check()
        raise AssertionError("ceiling not enforced")
    except SpendCapReached:
        pass


def test_verify_reruns_the_log():
    from knock.game import Game
    from knock.models import RandomModel
    g = Game("bus ticket", 3, RandomModel(), background=False)
    while g.view()["choices"]:
        g.choose(0)
    v = g.verify()
    assert v["verified"] and v["hashes"] == v["replayed"]


def test_concurrent_choices_on_one_game_get_409():
    """Two posts to /api/choose for the same game at once: one proceeds, the other gets a clean 409."""
    import json
    import threading
    import urllib.error
    import urllib.request

    from tests._fakehttp import serve

    class Slow:
        run_id = "slowgame"
        ending = None
        calls = 0

        def choose(self, index):
            Slow.calls += 1
            time.sleep(0.5)                     # a choice that takes a while (e.g. waiting on a shadow)
            return {"scene": "x", "kind": "scene", "text": "", "choices": []}

    api._games.clear()
    api._add_game(Slow())
    results = []

    def post():
        req = urllib.request.Request(url, data=json.dumps({"game": "slowgame", "index": 0}).encode())
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                results.append(r.status)
        except urllib.error.HTTPError as e:
            results.append(e.code)

    with serve(api.Handler) as base:
        url = base.removesuffix("/v1") + "/api/choose"
        ts = [threading.Thread(target=post) for _ in range(2)]
        ts[0].start(); time.sleep(0.1); ts[1].start()
        for t in ts:
            t.join()
    assert sorted(results) == [200, 409]
    assert Slow.calls == 1                     # the second choice never ran
