"""Hosted adapter against a fake OpenAI-compatible server: schema fallback, spend, cap."""

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from knock.game import Game
from knock.models import OpenAICompatModel, SpendCapReached, SpendLedger

KEY = "test-key-never-logged"


class FakeProvider(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"data": [{"id": "gemma-test"}]})

    def do_POST(self):
        assert self.headers["Authorization"] == f"Bearer {KEY}"
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.calls.append(req)
        if "response_format" in req:
            return self._send(400, {"error": {"message": "response_format not supported"}})
        rng = random.Random(len(FakeProvider.calls))
        last = req["messages"][-1]["content"]
        if "Allowed values" in last:
            allowed = json.loads(last.split("Allowed values: ", 1)[1])
            out = {}
            for k, v in allowed.items():
                if isinstance(v, list):
                    out[k] = rng.choice(v)
                elif v == "^[0-9]{4}$":
                    out[k] = "3917"
                elif isinstance(v, str) and v.startswith("^[A-Z]"):
                    out[k] = "STAY UP"
                else:
                    out[k] = "Hold still."
            content = "```json\n" + json.dumps(out) + "\n```"
        else:
            content = "plain narration"
        self._send(200, {"choices": [{"message": {"content": content}}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})


@pytest.fixture
def provider():
    FakeProvider.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_hosted_playthrough_records_spend(provider, tmp_path):
    ledger = SpendLedger(str(tmp_path / "spend.json"), cap_usd=10, price_in_per_m=0.18, price_out_per_m=0.50)
    base = OpenAICompatModel(provider, "gemma-test", KEY, ledger)
    g = Game("bus ticket", 3, base.for_run("run1"), background=False, run_id="run1")
    while g.view()["choices"]:
        g.choose(0)
    assert g.view()["kind"] == "ending"
    saved = json.loads((tmp_path / "spend.json").read_text())
    run = saved["runs"]["run1"]
    assert run["calls"] == saved["calls"] > 0
    assert run["usd"] == pytest.approx(run["calls"] * (1000 * 0.18 + 100 * 0.50) / 1e6)
    # response_format was refused once, then the adapter stopped sending it.
    assert sum("response_format" in c for c in FakeProvider.calls) == 1
    assert KEY not in json.dumps(g.record())


def test_spend_cap_is_hard(provider, tmp_path):
    ledger = SpendLedger(str(tmp_path / "spend.json"), cap_usd=0.0005, price_in_per_m=0.18, price_out_per_m=0.50)
    m = OpenAICompatModel(provider, "gemma-test", KEY, ledger)
    n = 0
    with pytest.raises(SpendCapReached):
        for _ in range(100):
            m.generate([{"role": "user", "content": "hi"}])
            n += 1
    assert n == 3                     # 3 calls x $0.00023 crosses $0.0005; the 4th is refused before sending
    assert ledger.total < 0.0005 + 0.00023
    assert not m.ping()


def test_ledger_persists_across_restarts(provider, tmp_path):
    path = str(tmp_path / "spend.json")
    l1 = SpendLedger(path, 10, 0.18, 0.50)
    OpenAICompatModel(provider, "gemma-test", KEY, l1).generate([{"role": "user", "content": "hi"}])
    l2 = SpendLedger(path, 10, 0.18, 0.50)
    assert l2.total == pytest.approx(l1.total) and l2.data["calls"] == 1
