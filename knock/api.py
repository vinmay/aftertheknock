"""HTTP server for the browser game. Standard library only.

    uv run knock-server            # http://localhost:8047

Live mode needs Ollama. Replay mode serves recorded runs from data/runs/ and needs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .game import Game, verify_replay
from .models import (DEFAULT_MODEL, RandomModel, ReplayModel, SpendCapReached, load_dotenv,
                     model_from_env)
from .state import ROOT

WEB = ROOT / "web"
RUNS = ROOT / "data" / "runs"
REPLAYS = RUNS / "replays"
MAX_GAMES = 200
MAP_SCENES = {"map", "final"}

NEW_GAMES_PER_IP_HOUR = 20
NEW_GAMES_PER_HOUR = int(os.environ.get("KNOCK_NEW_GAMES_PER_HOUR", "60"))   # all clients together
LIVE_CHECK_S = 60

_games: dict[str, Game] = {}
_lock = threading.Lock()
_model = None
_live = {"ok": False, "checked": 0.0}
_starts: dict[str, list[float]] = {}
_all_starts: list[float] = []
_last_seen: dict[str, float] = {}


def live_ok() -> bool:
    """Is generation available right now? Cached; hosted models are pinged, Ollama is asked for its version."""
    if isinstance(_model, RandomModel):
        return True                     # offline test mode
    if _model is None or not getattr(_model, "live", False):
        return False
    now = time.time()
    if now - _live["checked"] > LIVE_CHECK_S:
        if hasattr(_model, "ping"):
            ok = _model.ping()
        else:
            try:
                import urllib.request
                with urllib.request.urlopen(f"{_model.url}/api/version", timeout=5):
                    ok = True
            except Exception:
                ok = False
        _live.update(ok=ok, checked=now)
    return _live["ok"]


def _rate_ok(ip: str) -> bool:
    """Per-client and global limits on new live games per hour."""
    now = time.time()
    with _lock:
        _all_starts[:] = [t for t in _all_starts if now - t < 3600]
        recent = [t for t in _starts.get(ip, []) if now - t < 3600]
        _starts[ip] = recent
        if len(recent) >= NEW_GAMES_PER_IP_HOUR or len(_all_starts) >= NEW_GAMES_PER_HOUR:
            return False
        _starts[ip].append(now)
        _all_starts.append(now)
    return True


def _get(gid: str) -> Game:
    with _lock:
        g = _games.get(gid)
        if g is not None:
            _last_seen[gid] = time.time()
    if g is None:
        raise KeyError("unknown game")
    return g


def _add_game(g: Game) -> None:
    """Keep at most MAX_GAMES in memory, evicting the one idle longest."""
    with _lock:
        while len(_games) >= MAX_GAMES:
            idle = min(_games, key=lambda k: _last_seen.get(k, 0))
            _games.pop(idle)
            _last_seen.pop(idle, None)
        _games[g.run_id] = g
        _last_seen[g.run_id] = time.time()


def client_ip(handler) -> str:
    """Render appends the connecting client's address as the LAST X-Forwarded-For entry;
    earlier entries are whatever the client sent and can't be trusted."""
    xff = handler.headers.get("X-Forwarded-For", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    return parts[-1] if parts else handler.client_address[0]


def _save(g: Game) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"live_{g.run_id}.json").write_text(json.dumps(g.record()))


def _replay_list() -> list[dict]:
    out = []
    for p in sorted(REPLAYS.glob("*.json")):
        d = json.loads(p.read_text())
        out.append({"id": p.stem, "pocket": d["pocket"], "path": f"{d['fork1']} / {d['fork2']} / {d['ending']['which']}",
                    "seed": d["seed"], "model": d["model"].get("model")})
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if os.environ.get("KNOCK_HTTP_LOG"):
            super().log_message(fmt, *args)

    def _json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/status":
                info = _model.info() if _model is not None else {}
                return self._json({"live": live_ok(), "model": getattr(_model, "name", None),
                                   "hosted": bool(info.get("hosted")), "provider": info.get("provider"),
                                   "replays": len(_replay_list())})
            if u.path == "/api/replays":
                return self._json(_replay_list())
            if u.path == "/api/replay":
                rid = q["id"]
                if not rid.replace("_", "").isalnum():
                    raise KeyError("bad id")
                return self._json(json.loads((REPLAYS / f"{rid}.json").read_text()))
            if u.path == "/api/trace":
                return self._json(_get(q["game"]).trace(q["id"]))
            if u.path == "/api/map":
                g = _get(q["game"])
                if g.scene not in MAP_SCENES and not g.ending:
                    return self._json({"error": "the map is not open yet"}, HTTPStatus.FORBIDDEN)
                return self._json(g.map_data())
            if u.path == "/api/verify":
                g = _get(q["game"])
                if not g.ending:
                    return self._json({"error": "verify at the end of the night"}, HTTPStatus.FORBIDDEN)
                rec = getattr(g, "replay_of", None)
                if rec is not None:
                    return self._json({"verified": verify_replay(g, rec), "hashes": rec["state_hashes"],
                                       "replayed": g.record()["state_hashes"], "against": "recording"})
                return self._json(g.verify() | {"against": "this run's own log"})
            if u.path == "/api/evidence":
                g = _get(q["game"])
                return self._json({"log": g.log.records, "ops": g.timeline.ops,
                                   "model": g.log.model, "seed": g.seed,
                                   "crossings": [g.memory(x.intrusion_id) for x in g.timeline.intrusions]})
        except (KeyError, FileNotFoundError, StopIteration) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/new":
                if not live_ok():
                    return self._json({"error": "The live model isn't available right now.", "fallback": "replay"},
                                      HTTPStatus.SERVICE_UNAVAILABLE)
                ip = client_ip(self)
                if not _rate_ok(ip):
                    return self._json({"error": "Too many new nights from here; try a replay.", "fallback": "replay"},
                                      HTTPStatus.TOO_MANY_REQUESTS)
                seed = int(body.get("seed") or time.time_ns() % 1_000_000)
                run_id = uuid.uuid4().hex[:10]
                model = _model.for_run(run_id) if hasattr(_model, "for_run") else _model
                try:
                    model.ledger.check() if hasattr(model, "ledger") else None
                except SpendCapReached:
                    _live.update(ok=False, checked=time.time())
                    return self._json({"error": "The live model's budget is used up.", "fallback": "replay"},
                                      HTTPStatus.SERVICE_UNAVAILABLE)
                g = Game(str(body.get("pocket", ""))[:200], seed, model, run_id=run_id)
                _add_game(g)
                return self._json({"game": g.run_id, "seed": seed, "pocket": g.pocket, "view": g.view(),
                                   "model": f"{g.log.model.get('model')}{' (hosted)' if g.log.model.get('hosted') else ''} ·"})
            if u.path == "/api/replay/start":
                rid = str(body["id"])
                if not rid.replace("_", "").isalnum():
                    raise KeyError("bad id")
                rec = json.loads((REPLAYS / f"{rid}.json").read_text())
                g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False,
                         run_id=f"replay_{rec['run_id']}_{time.time_ns() % 10**6}")
                g.replay_of = rec
                _add_game(g)
                return self._json({"game": g.run_id, "seed": rec["seed"], "pocket": g.pocket,
                                   "view": g.view(), "replay": {
                                       "choices": [c["index"] for c in rec["choices"]],
                                       "model": rec["model"], "recorded_run": rec["run_id"]}})
            if u.path == "/api/choose":
                g = _get(body["game"])
                v = g.choose(int(body["index"]))
                rec = getattr(g, "replay_of", None)
                if g.ending and rec is not None:
                    v["verified"] = verify_replay(g, rec)
                elif g.ending:
                    _save(g)
                return self._json({"view": v})
        except (KeyError, ValueError, FileNotFoundError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)


def main(argv=None) -> None:
    global _model
    ap = argparse.ArgumentParser(prog="knock-server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8047)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model, 'random' for offline testing")
    ap.add_argument("--replay-only", action="store_true", help="serve recorded runs only")
    args = ap.parse_args(argv)
    load_dotenv()
    if not args.replay_only:
        _model = RandomModel() if args.model == "random" else model_from_env(args.model)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "replay only" if args.replay_only else f"live ({_model.info().get('model')}, {'hosted' if _model.info().get('hosted') else 'local'})"
    print(f"After the Knock on http://{args.host}:{args.port}  [{mode}]", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
