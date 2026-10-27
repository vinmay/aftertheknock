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
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .game import Game, verify_replay
from .models import DEFAULT_MODEL, OllamaModel, RandomModel, ReplayModel
from .state import ROOT

WEB = ROOT / "web"
RUNS = ROOT / "data" / "runs"
REPLAYS = RUNS / "replays"
MAX_GAMES = 200
MAP_SCENES = {"map", "final"}

_games: dict[str, Game] = {}
_lock = threading.Lock()
_model = None


def _get(gid: str) -> Game:
    with _lock:
        g = _games.get(gid)
    if g is None:
        raise KeyError("unknown game")
    return g


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
                return self._json({"live": _model is not None and getattr(_model, "live", False),
                                   "model": getattr(_model, "name", None), "replays": len(_replay_list())})
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
            if u.path == "/api/evidence":
                g = _get(q["game"])
                return self._json({"log": g.log.records, "ops": g.timeline.ops,
                                   "model": g.log.model, "seed": g.seed})
        except (KeyError, FileNotFoundError, StopIteration) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/new":
                if _model is None:
                    return self._json({"error": "live mode is off; try a replay"}, HTTPStatus.SERVICE_UNAVAILABLE)
                seed = int(body.get("seed") or time.time_ns() % 1_000_000)
                g = Game(str(body.get("pocket", ""))[:200], seed, _model)
                with _lock:
                    if len(_games) >= MAX_GAMES:
                        _games.pop(next(iter(_games)))
                    _games[g.run_id] = g
                return self._json({"game": g.run_id, "seed": seed, "pocket": g.pocket, "view": g.view()})
            if u.path == "/api/replay/start":
                rid = str(body["id"])
                if not rid.replace("_", "").isalnum():
                    raise KeyError("bad id")
                rec = json.loads((REPLAYS / f"{rid}.json").read_text())
                g = Game(rec["pocket_raw"], rec["seed"], ReplayModel(rec), background=False,
                         run_id=f"replay_{rec['run_id']}_{time.time_ns() % 10**6}")
                g.replay_of = rec
                with _lock:
                    _games[g.run_id] = g
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
    if not args.replay_only:
        _model = RandomModel() if args.model == "random" else OllamaModel(args.model)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    mode = "replay only" if args.replay_only else f"live ({args.model})"
    print(f"After the Knock on http://{args.host}:{args.port}  [{mode}]", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
