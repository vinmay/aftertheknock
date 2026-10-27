"""Model backends. The live path is a local Ollama server with JSON-schema output."""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.request
from dataclasses import dataclass

OLLAMA_URL = os.environ.get("KNOCK_OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("KNOCK_MODEL", "gemma4:e2b")


@dataclass
class Reply:
    text: str
    latency_s: float
    prompt_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "latency_s": round(self.latency_s, 3),
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
                "model": self.model}


def prompt_hash(messages: list[dict], schema: dict | None) -> str:
    blob = json.dumps({"m": messages, "s": schema}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class OllamaModel:
    live = True

    def __init__(self, name: str = DEFAULT_MODEL, url: str = OLLAMA_URL,
                 temperature: float = 0.8, num_ctx: int = 2048, timeout: float = 120):
        self.name, self.url = name, url.rstrip("/")
        self.temperature, self.num_ctx, self.timeout = temperature, num_ctx, timeout

    def info(self) -> dict:
        req = urllib.request.Request(f"{self.url}/api/show", data=json.dumps({"model": self.name}).encode())
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        det = d.get("details", {})
        return {"model": self.name, "quantization": det.get("quantization_level"),
                "parameter_size": det.get("parameter_size"), "family": det.get("family"),
                "digest": self._digest()}

    def _digest(self) -> str | None:
        with urllib.request.urlopen(f"{self.url}/api/tags", timeout=30) as r:
            for m in json.load(r).get("models", []):
                if m["name"] == self.name:
                    return m["digest"][:12]
        return None

    def generate(self, messages: list[dict], schema: dict | None = None, seed: int = 0,
                 max_tokens: int = 200) -> Reply:
        body = {"model": self.name, "messages": messages, "stream": False, "think": False,
                "keep_alive": "30m",
                "options": {"seed": seed, "temperature": self.temperature,
                            "num_ctx": self.num_ctx, "num_predict": max_tokens}}
        if schema is not None:
            body["format"] = schema
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.load(r)
        dt = time.perf_counter() - t0
        return Reply(d["message"]["content"], dt, d.get("prompt_eval_count", 0),
                     d.get("eval_count", 0), self.name)


class ScriptedModel:
    """Returns queued replies in order. For tests and deliberate-failure evals."""
    live = False
    name = "scripted"

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def info(self) -> dict:
        return {"model": self.name, "quantization": None}

    def generate(self, messages, schema=None, seed=0, max_tokens=200) -> Reply:
        self.calls.append(messages)
        if not self.replies:
            return Reply("{}", 0.0, model=self.name)
        r = self.replies.pop(0)
        return Reply(r if isinstance(r, str) else json.dumps(r), 0.0, model=self.name)


class RandomModel:
    """Offline stand-in: picks schema-valid values at random. Not used in the live path."""
    live = False
    name = "random"

    def info(self) -> dict:
        return {"model": self.name, "quantization": None}

    def generate(self, messages, schema=None, seed=0, max_tokens=200) -> Reply:
        rng = random.Random(f"{seed}:{prompt_hash(messages, schema)}")
        if schema is None:
            return Reply("The storm leans on the windows.", 0.0, model=self.name)
        out = {}
        for k, spec in schema["properties"].items():
            if "enum" in spec:
                out[k] = rng.choice(spec["enum"])
            elif spec.get("pattern") == "^[0-9]{4}$":
                out[k] = f"{rng.randrange(10000):04d}"
            else:
                out[k] = rng.choice(["", "Don't.", "You already let me in.", "It's cold down there."])
        return Reply(json.dumps(out), 0.0, model=self.name)


class ReplayModel:
    """Serves the outputs recorded in a run log, matched by prompt hash.

    Re-running a game against this model re-derives every state through the engine,
    so a replay is checked, not just shown: its state hashes must match the record.
    """
    live = False

    def __init__(self, record: dict):
        self.name = f"replay of {record['model'].get('model')}"
        self.recorded_model = record["model"]
        self.queues: dict[str, list[str]] = {}
        for r in record["log"]:
            if r["kind"] in ("proposal", "narration"):
                text = r.get("raw", r.get("text", ""))
                self.queues.setdefault(r["prompt_hash"], []).append(text)
        self.misses = 0

    def info(self) -> dict:
        return dict(self.recorded_model)

    def generate(self, messages, schema=None, seed=0, max_tokens=200) -> Reply:
        q = self.queues.get(prompt_hash(messages, schema))
        if not q:
            self.misses += 1
            return Reply("", 0.0, model=self.name)
        return Reply(q.pop(0), 0.0, model=self.name)


# ---------------------------------------------------------------- hosted (OpenAI-compatible)
class SpendCapReached(RuntimeError):
    pass


class SpendLedger:
    """Running spend for a hosted model, persisted to a JSON file and capped.

    The cap is checked before every call. The provider's own prepaid balance is the
    backstop if the file is lost (for example on a host with an ephemeral disk).
    """

    def __init__(self, path: str, cap_usd: float, price_in_per_m: float, price_out_per_m: float):
        import threading
        from pathlib import Path
        self.path = Path(path)
        self.cap = cap_usd
        self.price_in, self.price_out = price_in_per_m, price_out_per_m
        self.lock = threading.Lock()
        self.data = {"cap_usd": cap_usd, "price_in_per_m": price_in_per_m,
                     "price_out_per_m": price_out_per_m, "total_usd": 0.0, "calls": 0, "runs": {}}
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            self.data.update({k: saved[k] for k in ("total_usd", "calls", "runs") if k in saved})

    @property
    def total(self) -> float:
        return self.data["total_usd"]

    def check(self) -> None:
        if self.total >= self.cap:
            raise SpendCapReached(f"spend cap ${self.cap:.2f} reached (${self.total:.4f} spent)")

    def add(self, run_id: str | None, prompt_tokens: int, output_tokens: int) -> float:
        usd = prompt_tokens / 1e6 * self.price_in + output_tokens / 1e6 * self.price_out
        with self.lock:
            self.data["total_usd"] = round(self.data["total_usd"] + usd, 8)
            self.data["calls"] += 1
            run = self.data["runs"].setdefault(run_id or "unattributed",
                                               {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "usd": 0.0})
            run["calls"] += 1
            run["prompt_tokens"] += prompt_tokens
            run["output_tokens"] += output_tokens
            run["usd"] = round(run["usd"] + usd, 8)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)
        return usd


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


class OpenAICompatModel:
    """An open-weight model behind an OpenAI-compatible chat completions API.

    Configured only through environment variables (see .env.example). The API key is
    never logged or recorded.
    """
    live = True

    def __init__(self, base_url: str, model: str, api_key: str, ledger: SpendLedger,
                 temperature: float = 0.8, timeout: float = 60, run_id: str | None = None):
        self.url = base_url.rstrip("/")
        self.name = model
        self._key = api_key
        self.ledger = ledger
        self.temperature, self.timeout = temperature, timeout
        self.run_id = run_id
        self.schema_supported: bool | None = None

    @classmethod
    def from_env(cls) -> "OpenAICompatModel":
        e = os.environ
        ledger = SpendLedger(e.get("KNOCK_SPEND_FILE", "eval/results/spend.json"),
                             float(e.get("KNOCK_SPEND_CAP_USD", "10")),
                             float(e["KNOCK_PRICE_IN_PER_M"]), float(e["KNOCK_PRICE_OUT_PER_M"]))
        key = e.get("KNOCK_OPENAI_API_KEY") or e.get("DIGITALOCEAN_INFERENCE_KEY")
        if not key:
            raise SystemExit("set KNOCK_OPENAI_API_KEY (or DIGITALOCEAN_INFERENCE_KEY) for the hosted provider")
        return cls(e["KNOCK_OPENAI_BASE_URL"], e["KNOCK_OPENAI_MODEL"], key, ledger)

    def for_run(self, run_id: str) -> "OpenAICompatModel":
        m = OpenAICompatModel(self.url, self.name, self._key, self.ledger, self.temperature, self.timeout, run_id)
        m.schema_supported = self.schema_supported
        return m

    def info(self) -> dict:
        return {"model": self.name, "provider": self.url.split("//")[-1].split("/")[0],
                "quantization": "provider-managed", "hosted": True}

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(f"{self.url}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self._key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def ping(self) -> bool:
        try:
            self.ledger.check()
            req = urllib.request.Request(f"{self.url}/models", headers={"Authorization": f"Bearer {self._key}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except Exception:
            return False

    def generate(self, messages: list[dict], schema: dict | None = None, seed: int = 0,
                 max_tokens: int = 200) -> Reply:
        import urllib.error
        self.ledger.check()
        body = {"model": self.name, "messages": list(messages), "temperature": self.temperature,
                "seed": seed, "max_tokens": max_tokens}
        if schema is not None:
            if self.schema_supported is not False:
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": "event", "schema": schema, "strict": True}}
            else:
                body["messages"] = body["messages"] + [{"role": "user", "content":
                    "Reply with one JSON object only, no prose, with exactly these keys: "
                    + ", ".join(schema["properties"]) + ". Allowed values: "
                    + json.dumps({k: v.get("enum") or v.get("pattern") or "string"
                                  for k, v in schema["properties"].items()})}]
        t0 = time.perf_counter()
        try:
            d = self._post(body)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 422) and "response_format" in body:
                self.schema_supported = False
                return self.generate(messages, schema, seed, max_tokens)
            raise
        dt = time.perf_counter() - t0
        if schema is not None and "response_format" in body:
            self.schema_supported = True
        usage = d.get("usage") or {}
        pt, ot = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        self.ledger.add(self.run_id, pt, ot)
        text = d["choices"][0]["message"].get("content") or ""
        return Reply(_strip_fences(text) if schema is not None else text.strip(), dt, pt, ot, self.name)


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment variables win."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def model_from_env(default_name: str = DEFAULT_MODEL):
    """KNOCK_PROVIDER=ollama (default, local) or openai (hosted open-weight model)."""
    if os.environ.get("KNOCK_PROVIDER", "ollama") == "openai":
        return OpenAICompatModel.from_env()
    return OllamaModel(default_name)
